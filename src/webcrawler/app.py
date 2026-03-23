from __future__ import annotations

from dataclasses import asdict

from webcrawler.config import AppConfig
from webcrawler.crawler.fetcher import HttpFetcher
from webcrawler.crawler.service import WebCrawlerService
from webcrawler.crawler.storage import InMemoryCrawlPageStorage
from webcrawler.index.sqlite_storage import SQLiteIndexStorage
from webcrawler.queue.memory_queue import InMemoryQueue
from webcrawler.queue.scheduler import CrawlScheduler
from webcrawler.search.service import SearchService
from webcrawler.status.service import StatusService
from webcrawler.utils.persistence import JsonStateStore


class App:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.queue = InMemoryQueue(max_capacity=self.config.queue_capacity)
        self.scheduler = CrawlScheduler(queue_backend=self.queue, max_depth=self.config.max_depth)
        self.index_storage = SQLiteIndexStorage(self.config.index_db_path)
        self.page_storage = InMemoryCrawlPageStorage()
        self.status_service = StatusService()
        self.state_store = JsonStateStore(self.config.checkpoint_path)
        self.fetcher = HttpFetcher(config=self.config)
        self.crawler = WebCrawlerService(
            config=self.config,
            scheduler=self.scheduler,
            fetcher=self.fetcher,
            index_storage=self.index_storage,
            page_storage=self.page_storage,
            status_service=self.status_service,
            state_store=self.state_store,
        )
        self.search_service = SearchService(storage=self.index_storage)

    async def run_index(self, resume: bool = True) -> None:
        await self.crawler.run(resume=resume)

    async def index(
        self,
        origin: str,
        max_depth: int,
        resume: bool = False,
        crawl_run_id: str | None = None,
    ) -> None:
        await self.crawler.index(
            origin=origin,
            max_depth=max_depth,
            resume=resume,
            crawl_run_id=crawl_run_id,
        )

    def run_search(
        self,
        query: str,
        limit: int = 10,
        *,
        offset: int = 0,
        sort_by: str = "relevance",
        domain: str | None = None,
        crawl_run_id: str | None = None,
        indexed_from: str | None = None,
        indexed_to: str | None = None,
    ):
        self.index_storage.initialize()
        return self.search_service.search(
            query=query,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            domain=domain,
            crawl_run_id=crawl_run_id,
            indexed_from=indexed_from,
            indexed_to=indexed_to,
        )

    def get_status(self) -> dict:
        self.index_storage.initialize()
        status = self.status_service.snapshot()
        status["total_visited"] = status["seen_url_count"]
        status["indexed_documents"] = self.index_storage.count_documents()
        status["in_memory_pages"] = self.page_storage.count_pages()
        status["in_memory_crawl_runs"] = len(self.page_storage.list_runs())
        return status

    def list_crawled_pages(self, crawl_run_id: str | None = None) -> list[dict]:
        pages = self.page_storage.list_pages(crawl_run_id=crawl_run_id)
        pages.sort(key=lambda item: item.visit_order)
        return [asdict(page) for page in pages]

    def list_crawl_runs(self) -> list[dict]:
        return [asdict(run) for run in self.page_storage.list_runs()]
