from __future__ import annotations

from abc import ABC, abstractmethod

from webcrawler.models import Document, SearchHit


class BaseIndexStorage(ABC):
    @abstractmethod
    def initialize(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def upsert_document(self, document: Document) -> None:
        raise NotImplementedError

    @abstractmethod
    def search(
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
    ) -> list[SearchHit]:
        raise NotImplementedError

    @abstractmethod
    def count_documents(self) -> int:
        raise NotImplementedError
