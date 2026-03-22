from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any
from uuid import uuid4

from webcrawler.app import App
from webcrawler.config import AppConfig
from webcrawler.index.sqlite_storage import SQLiteIndexStorage


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class CrawlerJob:
    crawler_id: str
    config: AppConfig
    app: App | None = None
    state: str = "created"
    error: str | None = None
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    checkpoint_path: str = ""
    thread: Thread | None = None


class CrawlerManager:
    def __init__(self, base_config: AppConfig) -> None:
        self._base_config = base_config
        self._jobs: dict[str, CrawlerJob] = {}
        self._lock = Lock()
        self._meta_db_path = Path("./data/crawler_meta.sqlite3")
        self._meta_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._load_jobs_from_db()

    def create_crawler(
        self,
        *,
        origin: str,
        max_depth: int,
        max_pages: int,
        worker_count: int | None = None,
        requests_per_second: float,
        queue_capacity: int,
    ) -> str:
        crawler_id = uuid4().hex[:12]
        self._start_job(
            crawler_id=crawler_id,
            origin=origin,
            max_depth=max_depth,
            max_pages=max_pages,
            worker_count=worker_count or self._base_config.worker_count,
            requests_per_second=requests_per_second,
            queue_capacity=queue_capacity,
            resume=False,
        )
        return crawler_id

    def resume_crawler(self, crawler_id: str) -> bool:
        with self._meta_connect() as conn:
            row = conn.execute(
                """
                SELECT origin, max_depth, max_pages, worker_count, requests_per_second, queue_capacity
                FROM crawler_jobs
                WHERE crawler_id = ?
                """,
                (crawler_id,),
            ).fetchone()
        if row is None:
            return False

        self._start_job(
            crawler_id=crawler_id,
            origin=row[0],
            max_depth=int(row[1]),
            max_pages=int(row[2]),
            worker_count=int(row[3]),
            requests_per_second=float(row[4]),
            queue_capacity=int(row[5]),
            resume=True,
        )
        return True

    def delete_crawler(self, crawler_id: str) -> bool:
        with self._lock:
            running = self._jobs.get(crawler_id)
            if running and running.state == "running":
                return False
            self._jobs.pop(crawler_id, None)

        with self._meta_connect() as conn:
            conn.execute("DELETE FROM crawler_events WHERE crawler_id = ?", (crawler_id,))
            conn.execute("DELETE FROM crawler_jobs WHERE crawler_id = ?", (crawler_id,))
            conn.commit()

        storage = SQLiteIndexStorage(self._base_config.index_db_path)
        storage.initialize()
        with storage._connect() as conn:  # noqa: SLF001
            conn.execute("DELETE FROM documents WHERE crawl_run_id = ?", (crawler_id,))
            with self._meta_connect() as meta_conn:
                url_rows = meta_conn.execute(
                    """
                    SELECT DISTINCT url
                    FROM crawler_events
                    WHERE crawler_id = ? AND event_type = 'visit_start'
                    """,
                    (crawler_id,),
                ).fetchall()
            if url_rows:
                conn.executemany("DELETE FROM documents WHERE url = ?", url_rows)
            conn.commit()

        checkpoint = Path("./data/checkpoints") / f"{crawler_id}.json"
        if checkpoint.exists():
            checkpoint.unlink(missing_ok=True)
        return True

    def clear_all_data(self) -> bool:
        with self._lock:
            if any(job.state == "running" for job in self._jobs.values()):
                return False
            self._jobs.clear()

        with self._meta_connect() as conn:
            conn.execute("DELETE FROM crawler_events")
            conn.execute("DELETE FROM crawler_jobs")
            conn.commit()

        storage = SQLiteIndexStorage(self._base_config.index_db_path)
        storage.initialize()
        with storage._connect() as conn:  # noqa: SLF001
            conn.execute("DELETE FROM documents")
            conn.commit()

        checkpoint_dir = Path("./data/checkpoints")
        if checkpoint_dir.exists():
            for item in checkpoint_dir.glob("*.json"):
                item.unlink(missing_ok=True)
        return True

    def list_crawlers(self) -> list[dict[str, Any]]:
        with self._meta_connect() as conn:
            rows = conn.execute(
                """
                SELECT crawler_id, state, error, origin, max_depth, max_pages, created_at, started_at, finished_at
                FROM crawler_jobs
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [
            {
                "crawler_id": row[0],
                "state": row[1],
                "error": row[2],
                "origin": row[3],
                "max_depth": row[4],
                "max_pages": row[5],
                "created_at": row[6],
                "started_at": row[7],
                "finished_at": row[8],
            }
            for row in rows
        ]

    def filter_crawlers(
        self,
        *,
        state: str | None = None,
        domain: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        jobs = self.list_crawlers()
        output: list[dict[str, Any]] = []
        for job in jobs:
            if state and state != "all" and job["state"] != state:
                continue
            if domain and domain.strip() and domain.strip().lower() not in job["origin"].lower():
                continue
            if query and query.strip():
                q = query.strip().lower()
                if q not in job["crawler_id"].lower() and q not in job["origin"].lower():
                    continue
            output.append(job)
        return output

    def get_crawler_status(self, crawler_id: str) -> dict[str, Any] | None:
        with self._meta_connect() as conn:
            row = conn.execute(
                """
                SELECT crawler_id, state, error, origin, max_depth, max_pages, created_at, started_at, finished_at
                FROM crawler_jobs
                WHERE crawler_id = ?
                """,
                (crawler_id,),
            ).fetchone()
            if row is None:
                return None

            events = conn.execute(
                """
                SELECT event_order, event_type, depth, url, info, created_at
                FROM crawler_events
                WHERE crawler_id = ?
                ORDER BY id ASC
                """,
                (crawler_id,),
            ).fetchall()

        with self._lock:
            runtime = self._jobs.get(crawler_id)
        if runtime and runtime.app:
            status = runtime.app.get_status()
        else:
            status = self._build_persisted_status(crawler_id)

        event_log = []
        for e in events:
            info = json.loads(e[4]) if e[4] else {}
            event_log.append(
                {
                    "event_order": e[0],
                    "event_type": e[1],
                    "depth": e[2],
                    "url": e[3],
                    "created_at": e[5],
                    **info,
                }
            )
        visit_trace = [
            {
                "visit_order": item["event_order"],
                "url": item["url"],
                "depth": item["depth"],
                "status_code": item.get("status_code"),
            }
            for item in event_log
            if item["event_type"] == "visit_start"
        ]
        status["event_log"] = event_log
        status["visit_trace"] = visit_trace
        status.update(
            {
                "crawler_id": row[0],
                "state": row[1],
                "error": row[2],
                "origin": row[3],
                "max_depth": row[4],
                "max_pages": row[5],
                "created_at": row[6],
                "started_at": row[7],
                "finished_at": row[8],
            }
        )
        return status

    def search_with_filters(
        self,
        *,
        query: str,
        limit: int = 20,
        domain: str | None = None,
        crawl_run_id: str | None = None,
        indexed_from: str | None = None,
        indexed_to: str | None = None,
    ) -> list[dict[str, Any]]:
        storage = SQLiteIndexStorage(self._base_config.index_db_path)
        storage.initialize()
        hits = storage.search(
            query=query,
            limit=limit,
            domain=domain,
            crawl_run_id=crawl_run_id,
            indexed_from=indexed_from,
            indexed_to=indexed_to,
        )
        return [{"url": h.url, "title": h.title, "snippet": h.snippet, "score": h.score} for h in hits]

    def get_overview(self) -> dict[str, Any]:
        with self._meta_connect() as conn:
            total_created = int(conn.execute("SELECT COUNT(*) FROM crawler_jobs").fetchone()[0])
            active_crawlers = int(conn.execute("SELECT COUNT(*) FROM crawler_jobs WHERE state='running'").fetchone()[0])
            completed_crawlers = int(conn.execute("SELECT COUNT(*) FROM crawler_jobs WHERE state='completed'").fetchone()[0])
            failed_crawlers = int(conn.execute("SELECT COUNT(*) FROM crawler_jobs WHERE state='failed'").fetchone()[0])
            total_urls_visited = int(
                conn.execute("SELECT COUNT(*) FROM crawler_events WHERE event_type='visit_start'").fetchone()[0]
            )
            failed_urls = int(
                conn.execute("SELECT COUNT(*) FROM crawler_events WHERE event_type='visit_error'").fetchone()[0]
            )
            recent_rows = conn.execute(
                """
                SELECT event_order, event_type, depth, url, info, created_at
                FROM crawler_events
                ORDER BY id DESC
                LIMIT 10
                """
            ).fetchall()

        total_queue_size = 0
        with self._lock:
            for job in self._jobs.values():
                if job.app is not None and job.state == "running":
                    total_queue_size += int(job.app.get_status().get("queue_size", 0))

        storage = SQLiteIndexStorage(self._base_config.index_db_path)
        storage.initialize()
        indexed_documents = storage.count_documents()
        words_in_db = storage.count_words()

        recent_events = []
        for row in reversed(recent_rows):
            info = json.loads(row[4]) if row[4] else {}
            recent_events.append(
                {
                    "event_order": row[0],
                    "event_type": row[1],
                    "depth": row[2],
                    "url": row[3],
                    "created_at": row[5],
                    **info,
                }
            )

        return {
            "total_created": total_created,
            "active_crawlers": active_crawlers,
            "completed_crawlers": completed_crawlers,
            "failed_crawlers": failed_crawlers,
            "total_urls_visited": total_urls_visited,
            "failed_urls": failed_urls,
            "queue_size": total_queue_size,
            "indexed_documents": indexed_documents,
            "words_in_db": words_in_db,
            "recent_events": recent_events,
            "updated_at": _utc_now_iso(),
        }

    def _start_job(
        self,
        *,
        crawler_id: str,
        origin: str,
        max_depth: int,
        max_pages: int,
        worker_count: int,
        requests_per_second: float,
        queue_capacity: int,
        resume: bool,
    ) -> None:
        checkpoint_dir = Path("./data/checkpoints")
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = checkpoint_dir / f"{crawler_id}.json"
        config = AppConfig(
            origin_url=origin,
            max_depth=max_depth,
            max_pages=max_pages,
            request_timeout_seconds=self._base_config.request_timeout_seconds,
            requests_per_second=requests_per_second,
            worker_count=worker_count,
            queue_capacity=queue_capacity,
            max_content_chars=self._base_config.max_content_chars,
            user_agent=self._base_config.user_agent,
            index_db_path=self._base_config.index_db_path,
            checkpoint_path=checkpoint_path,
        )
        app = App(config)
        app.crawler.event_sink = self._build_event_sink(crawler_id)
        job = CrawlerJob(
            crawler_id=crawler_id,
            config=config,
            app=app,
            state="running",
            created_at=_utc_now_iso(),
            started_at=_utc_now_iso(),
            checkpoint_path=str(checkpoint_path),
        )

        thread = Thread(target=self._run_job, args=(crawler_id, resume), daemon=True)
        job.thread = thread
        with self._lock:
            self._jobs[crawler_id] = job
        self._upsert_job(job, keep_created_if_exists=True)
        thread.start()

    def _run_job(self, crawler_id: str, resume: bool) -> None:
        with self._lock:
            job = self._jobs[crawler_id]
        try:
            asyncio.run(
                job.app.index(
                    origin=job.config.origin_url,
                    max_depth=job.config.max_depth,
                    resume=resume,
                    crawl_run_id=crawler_id,
                )
            )
            job.state = "completed"
            job.finished_at = _utc_now_iso()
            job.error = None
        except Exception as exc:  # noqa: BLE001
            job.state = "failed"
            job.error = str(exc)
            job.finished_at = _utc_now_iso()
        finally:
            self._upsert_job(job)

    def _build_event_sink(self, crawler_id: str):
        def _sink(event: dict[str, Any]) -> None:
            info: dict[str, Any] = {}
            for k, v in event.items():
                if k not in {"event_order", "event_type", "depth", "url"}:
                    info[k] = v
            with self._meta_connect() as conn:
                conn.execute(
                    """
                    INSERT INTO crawler_events(crawler_id, event_order, event_type, depth, url, info, created_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        crawler_id,
                        int(event.get("event_order", 0)),
                        str(event.get("event_type", "")),
                        int(event.get("depth", 0)),
                        str(event.get("url", "")),
                        json.dumps(info, ensure_ascii=False),
                        _utc_now_iso(),
                    ),
                )
                conn.commit()

        return _sink

    def _build_persisted_status(self, crawler_id: str) -> dict[str, Any]:
        storage = SQLiteIndexStorage(self._base_config.index_db_path)
        storage.initialize()
        with storage._connect() as conn:  # noqa: SLF001
            indexed_documents = int(
                conn.execute("SELECT COUNT(*) FROM documents WHERE crawl_run_id = ?", (crawler_id,)).fetchone()[0]
            )
        with self._meta_connect() as conn:
            pages_crawled = int(
                conn.execute(
                    "SELECT COUNT(*) FROM crawler_events WHERE crawler_id = ? AND event_type = 'visit_start'",
                    (crawler_id,),
                ).fetchone()[0]
            )
            pages_failed = int(
                conn.execute(
                    "SELECT COUNT(*) FROM crawler_events WHERE crawler_id = ? AND event_type = 'visit_error'",
                    (crawler_id,),
                ).fetchone()[0]
            )
        return {
            "pages_crawled": pages_crawled,
            "pages_indexed": indexed_documents,
            "pages_failed": pages_failed,
            "queue_size": 0,
            "seen_url_count": pages_crawled,
            "active_workers": 0,
            "dropped_by_backpressure": 0,
            "is_running": False,
            "current_url": None,
            "total_visited": pages_crawled,
            "indexed_documents": indexed_documents,
            "in_memory_pages": 0,
            "in_memory_crawl_runs": 0,
            "started_at": "",
            "last_updated_at": "",
        }

    def _upsert_job(self, job: CrawlerJob, keep_created_if_exists: bool = False) -> None:
        created_at = job.created_at or _utc_now_iso()
        with self._meta_connect() as conn:
            old = conn.execute(
                "SELECT created_at FROM crawler_jobs WHERE crawler_id = ?",
                (job.crawler_id,),
            ).fetchone()
            if old and keep_created_if_exists:
                created_at = old[0]
            conn.execute(
                """
                INSERT INTO crawler_jobs(
                  crawler_id, state, error, origin, max_depth, max_pages, worker_count,
                  requests_per_second, queue_capacity, checkpoint_path, created_at, started_at, finished_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(crawler_id) DO UPDATE SET
                  state=excluded.state,
                  error=excluded.error,
                  origin=excluded.origin,
                  max_depth=excluded.max_depth,
                  max_pages=excluded.max_pages,
                  worker_count=excluded.worker_count,
                  requests_per_second=excluded.requests_per_second,
                  queue_capacity=excluded.queue_capacity,
                  checkpoint_path=excluded.checkpoint_path,
                  created_at=excluded.created_at,
                  started_at=excluded.started_at,
                  finished_at=excluded.finished_at
                """,
                (
                    job.crawler_id,
                    job.state,
                    job.error,
                    job.config.origin_url,
                    job.config.max_depth,
                    job.config.max_pages,
                    job.config.worker_count,
                    job.config.requests_per_second,
                    job.config.queue_capacity,
                    job.checkpoint_path,
                    created_at,
                    job.started_at,
                    job.finished_at,
                ),
            )
            conn.commit()

    def _init_db(self) -> None:
        with self._meta_connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS crawler_jobs(
                  crawler_id TEXT PRIMARY KEY,
                  state TEXT NOT NULL,
                  error TEXT,
                  origin TEXT NOT NULL,
                  max_depth INTEGER NOT NULL,
                  max_pages INTEGER NOT NULL,
                  worker_count INTEGER NOT NULL,
                  requests_per_second REAL NOT NULL,
                  queue_capacity INTEGER NOT NULL,
                  checkpoint_path TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  started_at TEXT,
                  finished_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS crawler_events(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  crawler_id TEXT NOT NULL,
                  event_order INTEGER NOT NULL,
                  event_type TEXT NOT NULL,
                  depth INTEGER NOT NULL,
                  url TEXT NOT NULL,
                  info TEXT,
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_crawler ON crawler_events(crawler_id, id)")
            conn.commit()

    def _load_jobs_from_db(self) -> None:
        with self._meta_connect() as conn:
            rows = conn.execute(
                """
                SELECT crawler_id, state, error, origin, max_depth, max_pages, worker_count,
                       requests_per_second, queue_capacity, checkpoint_path, created_at, started_at, finished_at
                FROM crawler_jobs
                """
            ).fetchall()
        with self._lock:
            for row in rows:
                config = AppConfig(
                    origin_url=row[3],
                    max_depth=int(row[4]),
                    max_pages=int(row[5]),
                    request_timeout_seconds=self._base_config.request_timeout_seconds,
                    requests_per_second=float(row[7]),
                    worker_count=int(row[6]),
                    queue_capacity=int(row[8]),
                    max_content_chars=self._base_config.max_content_chars,
                    user_agent=self._base_config.user_agent,
                    index_db_path=self._base_config.index_db_path,
                    checkpoint_path=Path(row[9]),
                )
                state = "interrupted" if row[1] == "running" else row[1]
                self._jobs[row[0]] = CrawlerJob(
                    crawler_id=row[0],
                    config=config,
                    app=None,
                    state=state,
                    error=row[2],
                    created_at=row[10],
                    started_at=row[11],
                    finished_at=row[12],
                    checkpoint_path=row[9],
                )
                if state != row[1]:
                    with self._meta_connect() as conn:
                        conn.execute(
                            "UPDATE crawler_jobs SET state = ? WHERE crawler_id = ?",
                            (state, row[0]),
                        )
                        conn.commit()

    def _meta_connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._meta_db_path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn
