from __future__ import annotations

from typing import Protocol

from webcrawler.models import CrawlRun, CrawlTask, CrawledPage, Document, SearchHit


class QueueBackend(Protocol):
    def enqueue(self, task: CrawlTask) -> bool:
        ...

    def dequeue(self) -> CrawlTask | None:
        ...

    def size(self) -> int:
        ...

    def export_pending(self) -> list[CrawlTask]:
        ...

    def import_pending(self, tasks: list[CrawlTask]) -> None:
        ...

    def clear(self) -> None:
        ...


class IndexStorage(Protocol):
    def initialize(self) -> None:
        ...

    def upsert_document(self, document: Document) -> None:
        ...

    def search(
        self,
        query: str,
        limit: int = 10,
        *,
        domain: str | None = None,
        crawl_run_id: str | None = None,
        indexed_from: str | None = None,
        indexed_to: str | None = None,
    ) -> list[SearchHit]:
        ...

    def count_documents(self) -> int:
        ...


class StateStore(Protocol):
    def save(self, seen: set[str], pending: list[CrawlTask]) -> None:
        ...

    def load(self) -> tuple[set[str], list[CrawlTask]]:
        ...


class CrawlPageStorage(Protocol):
    def start_run(self, origin: str, max_depth: int, crawl_run_id: str | None = None) -> str:
        ...

    def finish_run(self, crawl_run_id: str) -> None:
        ...

    def add_page(self, page: CrawledPage) -> None:
        ...

    def list_pages(self, crawl_run_id: str | None = None) -> list[CrawledPage]:
        ...

    def count_pages(self) -> int:
        ...

    def list_runs(self) -> list[CrawlRun]:
        ...

    def clear_all(self) -> None:
        ...
