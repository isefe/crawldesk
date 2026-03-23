from __future__ import annotations

import sqlite3
import string
import re
from pathlib import Path
from threading import RLock

from webcrawler.index.storage import BaseIndexStorage
from webcrawler.models import Document, SearchHit
from webcrawler.utils.url import html_to_text


class SQLiteIndexStorage(BaseIndexStorage):
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_dir = self.db_path.parent / "storage"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
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
                    origin TEXT NOT NULL DEFAULT '',
                    depth INTEGER NOT NULL DEFAULT 0,
                    indexed_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS word_entries (
                    word TEXT NOT NULL,
                    url TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    depth INTEGER NOT NULL,
                    frequency INTEGER NOT NULL,
                    PRIMARY KEY(word, url),
                    FOREIGN KEY(url) REFERENCES documents(url) ON DELETE CASCADE
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_word_entries_word ON word_entries(word)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_word_entries_url ON word_entries(url)")
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(documents)").fetchall()
            }
            if "crawl_run_id" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN crawl_run_id TEXT")
            if "origin" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN origin TEXT NOT NULL DEFAULT ''")
            if "depth" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN depth INTEGER NOT NULL DEFAULT 0")
            conn.commit()
            self._initialized = True

    def upsert_document(self, document: Document) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO documents(url, title, content, crawl_run_id, origin, depth, indexed_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                  title=excluded.title,
                  content=excluded.content,
                  crawl_run_id=excluded.crawl_run_id,
                  origin=excluded.origin,
                  depth=excluded.depth,
                  indexed_at=excluded.indexed_at
                """,
                (
                    document.url,
                    document.title,
                    document.content,
                    document.crawl_run_id,
                    document.origin,
                    document.depth,
                    document.indexed_at.isoformat(),
                ),
            )
            conn.execute("DELETE FROM word_entries WHERE url = ?", (document.url,))
            freq = self._word_frequencies(document.content)
            if freq:
                conn.executemany(
                    """
                    INSERT INTO word_entries(word, url, origin, depth, frequency)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(word, url) DO UPDATE SET
                      origin=excluded.origin,
                      depth=excluded.depth,
                      frequency=excluded.frequency
                    """,
                    [
                        (word, document.url, document.origin, int(document.depth), int(frequency))
                        for word, frequency in freq.items()
                    ],
                )
            conn.commit()
            self._write_storage_files(conn)

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
        if not query.strip():
            return []

        normalized_query = query.strip().lower()
        tokenized_query = [token for token in re.findall(r"[a-z0-9]+", normalized_query) if token]
        if len(tokenized_query) == 1:
            return self._search_word_entries(
                word=tokenized_query[0],
                limit=limit,
                offset=offset,
                sort_by=sort_by,
                domain=domain,
                crawl_run_id=crawl_run_id,
                indexed_from=indexed_from,
                indexed_to=indexed_to,
            )

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
        query_len = max(1, len(normalized_query))
        score_expr = (
            "(((LENGTH(LOWER(title)) - LENGTH(REPLACE(LOWER(title), ?, ''))) / ?) * 3.0) + "
            "(((LENGTH(LOWER(content)) - LENGTH(REPLACE(LOWER(content), ?, ''))) / ?) * 1.0)"
        )
        params_with_score = [*params, normalized_query, query_len, normalized_query, query_len]

        order_by_sql = "relevance_score DESC, indexed_at DESC"
        if sort_by == "recent":
            order_by_sql = "indexed_at DESC, relevance_score DESC"

        safe_limit = max(1, int(limit))
        safe_offset = max(0, int(offset))
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                f"""
                SELECT url, title, content, indexed_at, {score_expr} AS relevance_score
                FROM documents
                WHERE {where_sql}
                ORDER BY {order_by_sql}
                LIMIT ? OFFSET ?
                """,
                [*params_with_score, safe_limit, safe_offset],
            ).fetchall()

        hits: list[SearchHit] = []
        for url, title, content, _indexed_at, relevance_score in rows:
            clean_text = html_to_text(content)
            snippet = (clean_text or title)[:220].strip()
            score = float(relevance_score or 0.0)
            hits.append(SearchHit(url=url, title=title, snippet=snippet, score=score if score > 0 else 1.0))
        return hits

    def count_documents(self) -> int:
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
        return int(row[0] if row else 0)

    def count_words(self) -> int:
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT COUNT(*) FROM word_entries").fetchone()
        return int(row[0] if row else 0)

    def _search_word_entries(
        self,
        *,
        word: str,
        limit: int,
        offset: int,
        sort_by: str,
        domain: str | None,
        crawl_run_id: str | None,
        indexed_from: str | None,
        indexed_to: str | None,
    ) -> list[SearchHit]:
        where_parts = ["e.word = ?"]
        params: list[object] = [word]
        if domain and domain.strip():
            where_parts.append("d.url LIKE ?")
            params.append(f"%{domain.strip()}%")
        if crawl_run_id and crawl_run_id.strip():
            where_parts.append("d.crawl_run_id = ?")
            params.append(crawl_run_id.strip())
        if indexed_from and indexed_from.strip():
            where_parts.append("d.indexed_at >= ?")
            params.append(f"{indexed_from.strip()}T00:00:00")
        if indexed_to and indexed_to.strip():
            where_parts.append("d.indexed_at <= ?")
            params.append(f"{indexed_to.strip()}T23:59:59")

        where_sql = " AND ".join(where_parts)
        order_sql = "relevance_score DESC, d.indexed_at DESC"
        if sort_by == "recent":
            order_sql = "d.indexed_at DESC, relevance_score DESC"

        safe_limit = max(1, int(limit))
        safe_offset = max(0, int(offset))
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                f"""
                SELECT
                  d.url,
                  d.title,
                  d.content,
                  ((e.frequency * 10.0) + 1000.0 - (e.depth * 5.0)) AS relevance_score
                FROM word_entries e
                JOIN documents d ON d.url = e.url
                WHERE {where_sql}
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?
                """,
                [*params, safe_limit, safe_offset],
            ).fetchall()

        hits: list[SearchHit] = []
        for url, title, content, relevance_score in rows:
            clean_text = html_to_text(content)
            snippet = (clean_text or title)[:220].strip()
            hits.append(
                SearchHit(
                    url=url,
                    title=title,
                    snippet=snippet,
                    score=float(relevance_score or 0.0),
                )
            )
        return hits

    def _word_frequencies(self, raw_html: str) -> dict[str, int]:
        text = html_to_text(raw_html).lower()
        tokens = re.findall(r"[a-z0-9]+", text)
        freq: dict[str, int] = {}
        for token in tokens:
            freq[token] = freq.get(token, 0) + 1
        return freq

    def ensure_storage_files(self) -> None:
        for ch in string.ascii_lowercase:
            path = self.storage_dir / f"{ch}.data"
            if not path.exists():
                path.write_text("", encoding="utf-8")

    def _write_storage_files(self, conn: sqlite3.Connection) -> None:
        has_existing_files = any((self.storage_dir / f"{ch}.data").exists() for ch in string.ascii_lowercase)
        if not has_existing_files:
            return
        file_buffers: dict[str, list[str]] = {ch: [] for ch in string.ascii_lowercase}
        rows = conn.execute(
            """
            SELECT word, url, origin, depth, frequency
            FROM word_entries
            ORDER BY word ASC, url ASC
            """
        ).fetchall()
        for word, url, origin, depth, frequency in rows:
            if not word:
                continue
            first = str(word)[0].lower()
            if first not in file_buffers:
                continue
            line = f"{word} {url} {origin} {int(depth)} {int(frequency)}"
            file_buffers[first].append(line)

        for ch in string.ascii_lowercase:
            path = self.storage_dir / f"{ch}.data"
            content = "\n".join(file_buffers[ch]).strip()
            if content:
                path.write_text(content + "\n", encoding="utf-8")
            else:
                path.write_text("", encoding="utf-8")
