from __future__ import annotations

import uuid
from threading import Lock

from webcrawler.models import CrawlRun, CrawledPage, utc_now
from webcrawler.types import CrawlPageStorage


class InMemoryCrawlPageStorage(CrawlPageStorage):
    def __init__(self) -> None:
        self._pages: list[CrawledPage] = []
        self._runs: list[CrawlRun] = []
        self._lock = Lock()

    def start_run(self, origin: str, max_depth: int, crawl_run_id: str | None = None) -> str:
        with self._lock:
            crawl_run_id = crawl_run_id or uuid.uuid4().hex[:12]
            self._runs.append(CrawlRun(crawl_run_id=crawl_run_id, origin=origin, max_depth=max_depth))
            return crawl_run_id

    def finish_run(self, crawl_run_id: str) -> None:
        with self._lock:
            for run in self._runs:
                if run.crawl_run_id == crawl_run_id:
                    run.finished_at = utc_now()
                    run.pages_indexed = sum(1 for p in self._pages if p.crawl_run_id == crawl_run_id)
                    break

    def add_page(self, page: CrawledPage) -> None:
        with self._lock:
            self._pages.append(page)

    def list_pages(self, crawl_run_id: str | None = None) -> list[CrawledPage]:
        with self._lock:
            if crawl_run_id is None:
                return list(self._pages)
            return [page for page in self._pages if page.crawl_run_id == crawl_run_id]

    def list_runs(self) -> list[CrawlRun]:
        with self._lock:
            return list(self._runs)

    def count_pages(self) -> int:
        with self._lock:
            return len(self._pages)

    def clear_all(self) -> None:
        with self._lock:
            self._pages.clear()
            self._runs.clear()
