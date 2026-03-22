from __future__ import annotations

from webcrawler.models import SearchHit
from webcrawler.types import IndexStorage


class SearchService:
    def __init__(self, storage: IndexStorage) -> None:
        self.storage = storage

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
        return self.storage.search(
            query=query,
            limit=limit,
            domain=domain,
            crawl_run_id=crawl_run_id,
            indexed_from=indexed_from,
            indexed_to=indexed_to,
        )
