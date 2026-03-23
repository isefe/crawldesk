from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class CrawlTask:
    url: str
    depth: int
    origin: str | None = None
    parent_url: str | None = None


@dataclass(slots=True)
class CrawlResult:
    url: str
    depth: int
    status_code: int | None
    content: str
    content_type: str | None = None
    title: str | None = None
    links: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(slots=True)
class Document:
    url: str
    title: str
    content: str
    crawl_run_id: str | None = None
    origin: str = ""
    depth: int = 0
    indexed_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    score: float


@dataclass(slots=True)
class CrawledPage:
    crawl_run_id: str
    visit_order: int
    url: str
    depth: int
    origin: str
    title: str
    content: str
    status_code: int | None
    crawled_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class CrawlRun:
    crawl_run_id: str
    origin: str
    max_depth: int
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None
    pages_indexed: int = 0


@dataclass(slots=True)
class CrawlerStats:
    started_at: datetime = field(default_factory=utc_now)
    last_updated_at: datetime = field(default_factory=utc_now)
    pages_crawled: int = 0
    pages_indexed: int = 0
    pages_failed: int = 0
    queue_size: int = 0
    seen_url_count: int = 0
    active_workers: int = 0
    dropped_by_backpressure: int = 0
    is_running: bool = False
    current_url: str | None = None
