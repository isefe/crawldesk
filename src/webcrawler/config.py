from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AppConfig:
    origin_url: str = "https://example.com"
    max_depth: int = 1
    max_pages: int = 100
    request_timeout_seconds: float = 8.0
    requests_per_second: float = 3.0
    worker_count: int = 1
    queue_capacity: int = 2000
    max_content_chars: int = 12000
    user_agent: str = "single-machine-webcrawler/0.1"
    index_db_path: Path = Path("./data/index.sqlite3")
    checkpoint_path: Path = Path("./data/checkpoint.json")

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            origin_url=os.getenv("CRAWLER_ORIGIN_URL", "https://example.com"),
            max_depth=int(os.getenv("CRAWLER_MAX_DEPTH", "1")),
            max_pages=int(os.getenv("CRAWLER_MAX_PAGES", "100")),
            request_timeout_seconds=float(os.getenv("CRAWLER_TIMEOUT", "8.0")),
            requests_per_second=float(os.getenv("CRAWLER_REQUESTS_PER_SECOND", "3.0")),
            worker_count=int(os.getenv("CRAWLER_WORKER_COUNT", "1")),
            queue_capacity=int(os.getenv("CRAWLER_QUEUE_CAPACITY", "2000")),
            max_content_chars=int(os.getenv("CRAWLER_MAX_CONTENT_CHARS", "12000")),
            user_agent=os.getenv("CRAWLER_USER_AGENT", "single-machine-webcrawler/0.1"),
            index_db_path=Path(os.getenv("CRAWLER_INDEX_DB_PATH", "./data/index.sqlite3")),
            checkpoint_path=Path(os.getenv("CRAWLER_CHECKPOINT_PATH", "./data/checkpoint.json")),
        )
