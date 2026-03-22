from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock

from webcrawler.index.storage import BaseIndexStorage
from webcrawler.models import Document, SearchHit
from webcrawler.utils.url import html_to_text


class SQLiteIndexStorage(BaseIndexStorage):
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._lock = RLock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("PRAGMA temp_store=MEMORY;")
                conn.execute("PRAGMA foreign_keys=ON;")
                self._conn = conn
            return self._conn

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return
            conn = self._connect()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    url TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    crawl_run_id TEXT,
                    indexed_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(documents)").fetchall()
            }
            if "crawl_run_id" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN crawl_run_id TEXT")
            conn.commit()
            self._initialized = True

    def upsert_document(self, document: Document) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO documents(url, title, content, crawl_run_id, indexed_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                  title=excluded.title,
                  content=excluded.content,
                  crawl_run_id=excluded.crawl_run_id,
                  indexed_at=excluded.indexed_at
                """,
                (
                    document.url,
                    document.title,
                    document.content,
                    document.crawl_run_id,
                    document.indexed_at.isoformat(),
                ),
            )
            conn.commit()

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
        if not query.strip():
            return []

        like = f"%{query.strip()}%"
        where_parts = ["(title LIKE ? OR content LIKE ?)"]
        params: list[object] = [like, like]
        if domain and domain.strip():
            where_parts.append("url LIKE ?")
            params.append(f"%{domain.strip()}%")
        if crawl_run_id and crawl_run_id.strip():
            where_parts.append("crawl_run_id = ?")
            params.append(crawl_run_id.strip())
        if indexed_from and indexed_from.strip():
            where_parts.append("indexed_at >= ?")
            params.append(f"{indexed_from.strip()}T00:00:00")
        if indexed_to and indexed_to.strip():
            where_parts.append("indexed_at <= ?")
            params.append(f"{indexed_to.strip()}T23:59:59")

        where_sql = " AND ".join(where_parts)
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                f"""
                SELECT url, title, content
                FROM documents
                WHERE {where_sql}
                ORDER BY indexed_at DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()

        hits: list[SearchHit] = []
        for url, title, content in rows:
            clean_text = html_to_text(content)
            snippet = (clean_text or title)[:220].strip()
            hits.append(SearchHit(url=url, title=title, snippet=snippet, score=1.0))
        return hits

    def count_documents(self) -> int:
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
        return int(row[0] if row else 0)

    def count_words(self) -> int:
        with self._lock:
            conn = self._connect()
            rows = conn.execute("SELECT content FROM documents").fetchall()
        total = 0
        for (content,) in rows:
            total += len(html_to_text(content).split())
        return total
