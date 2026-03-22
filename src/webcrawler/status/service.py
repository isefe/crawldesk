from __future__ import annotations

from dataclasses import asdict
from threading import Lock
from typing import Any

from webcrawler.models import CrawlerStats, utc_now


class StatusService:
    def __init__(self) -> None:
        self._stats = CrawlerStats()
        self._lock = Lock()

    def mark_started(self) -> None:
        with self._lock:
            self._stats.is_running = True
            self._stats.started_at = utc_now()
            self._stats.last_updated_at = utc_now()
            self._stats.active_workers = 0
            self._stats.current_url = None

    def mark_stopped(self) -> None:
        with self._lock:
            self._stats.is_running = False
            self._stats.current_url = None
            self._stats.last_updated_at = utc_now()

    def update_queue_size(self, size: int) -> None:
        with self._lock:
            self._stats.queue_size = size
            self._stats.last_updated_at = utc_now()

    def update_seen_count(self, count: int) -> None:
        with self._lock:
            self._stats.seen_url_count = count
            self._stats.last_updated_at = utc_now()

    def mark_page_started(self, url: str) -> None:
        with self._lock:
            self._stats.current_url = url
            self._stats.last_updated_at = utc_now()

    def mark_page_indexed(self) -> None:
        with self._lock:
            self._stats.pages_crawled += 1
            self._stats.pages_indexed += 1
            self._stats.last_updated_at = utc_now()

    def mark_page_failed(self) -> None:
        with self._lock:
            self._stats.pages_crawled += 1
            self._stats.pages_failed += 1
            self._stats.last_updated_at = utc_now()

    def set_active_workers(self, count: int) -> None:
        with self._lock:
            self._stats.active_workers = max(0, count)
            self._stats.last_updated_at = utc_now()

    def increment_dropped_by_backpressure(self) -> None:
        with self._lock:
            self._stats.dropped_by_backpressure += 1
            self._stats.last_updated_at = utc_now()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            data = asdict(self._stats)
        data["started_at"] = data["started_at"].isoformat()
        data["last_updated_at"] = data["last_updated_at"].isoformat()
        return data
