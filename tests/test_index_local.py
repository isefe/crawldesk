from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from webcrawler.app import App
from webcrawler.config import AppConfig


class LocalCrawlerIndexTest(unittest.TestCase):
    def test_index_crawls_without_duplicates_and_respects_depth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "index.html").write_text(
                '<html><head><title>Home</title></head><body>'
                '<a href="/a.html">A</a>'
                '<a href="/a.html#frag">A2</a>'
                '<a href="/b.html?x=2&y=1">B</a>'
                "</body></html>",
                encoding="utf-8",
            )
            (root / "a.html").write_text(
                '<html><head><title>A</title></head><body>'
                '<a href="/c.html">C</a>'
                "</body></html>",
                encoding="utf-8",
            )
            (root / "b.html").write_text("<html><head><title>B</title></head><body>B</body></html>", encoding="utf-8")
            (root / "c.html").write_text("<html><head><title>C</title></head><body>C</body></html>", encoding="utf-8")

            old_cwd = os.getcwd()
            os.chdir(tmp_dir)
            server: ThreadingHTTPServer | None = None
            try:
                try:
                    server = ThreadingHTTPServer(("127.0.0.1", 0), SimpleHTTPRequestHandler)
                except PermissionError:
                    self.skipTest("Socket bind is not permitted in this environment")
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                origin = f"http://127.0.0.1:{server.server_port}/index.html"

                config = AppConfig(
                    origin_url=origin,
                    max_depth=1,
                    max_pages=20,
                    worker_count=3,
                    queue_capacity=50,
                    requests_per_second=20,
                    index_db_path=Path(tmp_dir) / "data" / "index.sqlite3",
                    checkpoint_path=Path(tmp_dir) / "data" / "checkpoint.json",
                )
                app = App(config)
                asyncio.run(app.index(origin=origin, max_depth=1, resume=False))
                status = app.get_status()
                pages = app.list_crawled_pages()
            finally:
                if server is not None:
                    server.shutdown()
                    server.server_close()
                os.chdir(old_cwd)

            urls = [page["url"] for page in pages]
            self.assertGreaterEqual(status["total_visited"], 3)
            self.assertEqual(len(urls), len(set(urls)))
            self.assertTrue(any(url.endswith("/index.html") for url in urls))
            self.assertFalse(any(url.endswith("/c.html") for url in urls))


if __name__ == "__main__":
    unittest.main()
