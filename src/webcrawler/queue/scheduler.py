from __future__ import annotations

from threading import Lock

from webcrawler.models import CrawlTask
from webcrawler.types import QueueBackend
from webcrawler.utils.url import normalize_url


class CrawlScheduler:
    def __init__(self, queue_backend: QueueBackend, max_depth: int) -> None:
        self.queue = queue_backend
        self.max_depth = max_depth
        self._seen: set[str] = set()
        self._lock = Lock()

    def seed(self, origin_url: str) -> None:
        self.schedule(CrawlTask(url=origin_url, depth=0, origin=origin_url))

    def reset(self, max_depth: int | None = None) -> None:
        if max_depth is not None:
            self.max_depth = max_depth
        with self._lock:
            self._seen.clear()
        self.queue.clear()

    def restore(self, seen: set[str], pending: list[CrawlTask]) -> None:
        with self._lock:
            self._seen = set()
            for url in seen:
                normalized = normalize_url(url)
                if normalized is not None:
                    self._seen.add(normalized)
        self.queue.import_pending(pending)

    def seen_urls(self) -> set[str]:
        with self._lock:
            return set(self._seen)

    def schedule(self, task: CrawlTask) -> bool:
        if task.depth > self.max_depth:
            return False

        normalized = normalize_url(task.url)
        if normalized is None:
            return False
        origin = normalize_url(task.origin or task.url)
        if origin is None:
            return False

        with self._lock:
            if normalized in self._seen:
                return False
            self._seen.add(normalized)

        enqueued = self.queue.enqueue(
            CrawlTask(
                url=normalized,
                depth=task.depth,
                origin=origin,
                parent_url=task.parent_url,
            )
        )
        if enqueued:
            return True

        with self._lock:
            self._seen.discard(normalized)
        return False

    def next_task(self) -> CrawlTask | None:
        return self.queue.dequeue()

    def pending(self) -> list[CrawlTask]:
        return self.queue.export_pending()

    def queue_size(self) -> int:
        return self.queue.size()
