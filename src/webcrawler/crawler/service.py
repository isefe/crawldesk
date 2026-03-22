from __future__ import annotations

import asyncio
import logging
from threading import Lock
from typing import Any, Callable

from webcrawler.config import AppConfig
from webcrawler.crawler.fetcher import HttpFetcher
from webcrawler.crawler.rate_limiter import AsyncRateLimiter
from webcrawler.models import CrawlTask, CrawledPage, Document
from webcrawler.queue.scheduler import CrawlScheduler
from webcrawler.status.service import StatusService
from webcrawler.types import CrawlPageStorage, IndexStorage, StateStore
from webcrawler.utils.url import normalize_url

logger = logging.getLogger(__name__)


class WebCrawlerService:
    def __init__(
        self,
        config: AppConfig,
        scheduler: CrawlScheduler,
        fetcher: HttpFetcher,
        index_storage: IndexStorage,
        page_storage: CrawlPageStorage,
        status_service: StatusService,
        state_store: StateStore,
    ) -> None:
        self.config = config
        self.scheduler = scheduler
        self.fetcher = fetcher
        self.index_storage = index_storage
        self.page_storage = page_storage
        self.status_service = status_service
        self.state_store = state_store
        self._counter_lock = Lock()
        self._pages_crawled = 0
        self._active_workers = 0
        self._visit_order = 0
        self._event_order = 0
        self._stop_event = asyncio.Event()
        self.event_sink: Callable[[dict[str, Any]], None] | None = None

    async def run(self, resume: bool = True) -> None:
        await self.index(origin=self.config.origin_url, max_depth=self.config.max_depth, resume=resume)

    async def index(
        self,
        origin: str,
        max_depth: int,
        resume: bool = False,
        crawl_run_id: str | None = None,
    ) -> None:
        normalized_origin = normalize_url(origin)
        if normalized_origin is None:
            raise ValueError(f"Invalid origin URL: {origin}")

        self.scheduler.reset(max_depth=max_depth)
        self._pages_crawled = 0
        self._active_workers = 0
        self._visit_order = 0
        self._event_order = 0
        self._stop_event = asyncio.Event()
        crawl_run_id = self.page_storage.start_run(
            origin=normalized_origin,
            max_depth=max_depth,
            crawl_run_id=crawl_run_id,
        )

        self.index_storage.initialize()
        self._bootstrap(origin=normalized_origin, resume=resume)
        self.status_service.mark_started()
        self.status_service.update_queue_size(self.scheduler.queue_size())
        self.status_service.update_seen_count(len(self.scheduler.seen_urls()))

        rate_limiter = AsyncRateLimiter(self.config.requests_per_second)
        # Strict BFS guarantee: keep a single consumer over FIFO queue.
        # Multi-worker crawling can interleave completion order and break strict BFS traversal order.
        workers = [
            asyncio.create_task(self._worker_loop(rate_limiter=rate_limiter, crawl_run_id=crawl_run_id))
        ]
        await asyncio.gather(*workers)

        self._save_checkpoint()
        self.page_storage.finish_run(crawl_run_id)
        self.status_service.mark_stopped()

    def _bootstrap(self, origin: str, resume: bool) -> None:
        if resume:
            seen, pending = self.state_store.load()
            if pending:
                self.scheduler.restore(seen=seen, pending=pending)
                for task in pending:
                    self._emit_event(
                        event_type="queue_restored",
                        url=task.url,
                        depth=task.depth,
                        extra={"source": "checkpoint"},
                    )
                return
        is_seeded = self.scheduler.schedule(CrawlTask(url=origin, depth=0, origin=origin))
        if is_seeded:
            self._emit_event(
                event_type="queue_enqueue",
                url=origin,
                depth=0,
                extra={"source": "seed"},
            )

    def _save_checkpoint(self) -> None:
        self.state_store.save(
            seen=self.scheduler.seen_urls(),
            pending=self.scheduler.pending(),
        )

    async def _worker_loop(self, rate_limiter: AsyncRateLimiter, crawl_run_id: str) -> None:
        while not self._stop_event.is_set():
            task = self.scheduler.next_task()
            if task is None:
                if self._is_fully_idle():
                    self._stop_event.set()
                    return
                await asyncio.sleep(0.03)
                continue

            if self._reached_page_limit():
                self._stop_event.set()
                return

            self._set_worker_activity(increase=True)
            self._emit_event(
                event_type="visit_start",
                url=task.url,
                depth=task.depth,
                extra={"origin": task.origin, "parent_url": task.parent_url},
            )
            self.status_service.mark_page_started(task.url)
            self.status_service.update_queue_size(self.scheduler.queue_size())
            self.status_service.update_seen_count(len(self.scheduler.seen_urls()))

            try:
                await rate_limiter.acquire()
                result = await self.fetcher.fetch(task)
                self._increase_crawled_counter()
                visit_order = self._next_visit_order()
                self.page_storage.add_page(
                    CrawledPage(
                        crawl_run_id=crawl_run_id,
                        visit_order=visit_order,
                        url=result.url,
                        depth=task.depth,
                        origin=task.origin or task.url,
                        title=result.title or result.url,
                        content=result.content,
                        status_code=result.status_code,
                    )
                )
                if result.error:
                    self._emit_event(
                        event_type="visit_error",
                        url=task.url,
                        depth=task.depth,
                        extra={"error": result.error, "status_code": result.status_code},
                    )
                    if result.error != "unsupported-content-type":
                        logger.warning("Fetch failed for %s: %s", task.url, result.error)
                        self.status_service.mark_page_failed()
                    continue

                self.index_storage.upsert_document(
                    Document(
                        url=result.url,
                        title=result.title or result.url,
                        content=result.content,
                        crawl_run_id=crawl_run_id,
                    )
                )
                self.status_service.mark_page_indexed()
                self._emit_event(
                    event_type="visit_done",
                    url=task.url,
                    depth=task.depth,
                    extra={"status_code": result.status_code},
                )

                if task.depth < self.scheduler.max_depth:
                    for link in result.links:
                        is_added = self.scheduler.schedule(
                            CrawlTask(
                                url=link,
                                depth=task.depth + 1,
                                origin=task.origin or task.url,
                                parent_url=task.url,
                            )
                        )
                        if is_added:
                            self._emit_event(
                                event_type="queue_enqueue",
                                url=link,
                                depth=task.depth + 1,
                                extra={"source": task.url},
                            )
                        if not is_added and self.scheduler.queue_size() >= self.config.queue_capacity:
                            self.status_service.increment_dropped_by_backpressure()
                            self._emit_event(
                                event_type="queue_drop_backpressure",
                                url=link,
                                depth=task.depth + 1,
                                extra={"source": task.url},
                            )
            finally:
                self._set_worker_activity(increase=False)
                self.status_service.update_queue_size(self.scheduler.queue_size())
                self.status_service.update_seen_count(len(self.scheduler.seen_urls()))
                if self._reached_page_limit():
                    self._stop_event.set()
                self._save_checkpoint()

    def _set_worker_activity(self, increase: bool) -> None:
        with self._counter_lock:
            if increase:
                self._active_workers += 1
            else:
                self._active_workers = max(0, self._active_workers - 1)
            active_workers = self._active_workers
        self.status_service.set_active_workers(active_workers)

    def _increase_crawled_counter(self) -> None:
        with self._counter_lock:
            self._pages_crawled += 1

    def _reached_page_limit(self) -> bool:
        with self._counter_lock:
            return self._pages_crawled >= self.config.max_pages

    def _next_visit_order(self) -> int:
        with self._counter_lock:
            self._visit_order += 1
            return self._visit_order

    def _next_event_order(self) -> int:
        with self._counter_lock:
            self._event_order += 1
            return self._event_order

    def _emit_event(
        self,
        *,
        event_type: str,
        url: str,
        depth: int,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if self.event_sink is None:
            return
        payload = {
            "event_order": self._next_event_order(),
            "event_type": event_type,
            "url": url,
            "depth": depth,
        }
        if extra:
            payload.update(extra)
        self.event_sink(payload)

    def _is_fully_idle(self) -> bool:
        with self._counter_lock:
            return self.scheduler.queue_size() == 0 and self._active_workers == 0
