from __future__ import annotations

from collections import deque
from threading import Lock

from webcrawler.models import CrawlTask
from webcrawler.types import QueueBackend


class InMemoryQueue(QueueBackend):
    def __init__(self, max_capacity: int = 2000) -> None:
        self._queue: deque[CrawlTask] = deque()
        self._max_capacity = max(1, max_capacity)
        self._lock = Lock()

    def enqueue(self, task: CrawlTask) -> bool:
        with self._lock:
            if len(self._queue) >= self._max_capacity:
                return False
            self._queue.append(task)
            return True

    def dequeue(self) -> CrawlTask | None:
        with self._lock:
            if not self._queue:
                return None
            return self._queue.popleft()

    def size(self) -> int:
        with self._lock:
            return len(self._queue)

    def export_pending(self) -> list[CrawlTask]:
        with self._lock:
            return list(self._queue)

    def import_pending(self, tasks: list[CrawlTask]) -> None:
        with self._lock:
            for task in tasks:
                if len(self._queue) >= self._max_capacity:
                    break
                self._queue.append(task)

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()
