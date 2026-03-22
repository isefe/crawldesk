# CrawlDesk — Full Product Requirements Document (PRD)

Version: `1.0`  
Status: `Implementation-grade specification`  
Language: English  
Owner: `istemihan`  
Signature: `Built by istemihan`

---

## 1. Executive Summary

CrawlDesk is a single-machine crawling and indexing product with an operator-friendly web control panel and CLI.  
It crawls websites from an origin URL, indexes fetched HTML pages, supports search on indexed data, and preserves crawler/job history across restarts using SQLite + checkpoint files.

This document is designed so an AI engineer can rebuild the product end-to-end with no additional context.

---

## 2. Product Goals and Constraints

## 2.1 Goals
- Crawl from an origin URL up to max depth.
- Avoid duplicate URL visits per run.
- Persist indexed content for cumulative search.
- Provide browser UI for creating crawlers, searching, and status monitoring.
- Persist crawler metadata/events across app restarts.
- Keep system simple, maintainable, and framework-light.

## 2.2 Constraints
- Single machine only.
- Python standard library preferred for crawling/parsing.
- No heavy crawler framework (Scrapy etc.) in core crawling flow.
- Must remain operable from both CLI and web UI.

## 2.3 Non-goals (current)
- Distributed crawling.
- Sophisticated ranking/retrieval model (BM25/vector/LLM ranking).
- Full robots policy workflow UI.
- Pause/resume for actively running worker thread interruption (currently not exposed in UI).

---

## 3. System Architecture

High-level components:
- `Crawler Engine` (scheduler + queue + fetcher + worker loop + rate limiter)
- `Index Storage` (SQLite `documents`)
- `Crawler Metadata Storage` (SQLite `crawler_jobs` and `crawler_events`)
- `Checkpoint Storage` (JSON per crawler)
- `Web UI Server` (built on `http.server`)
- `CLI Entry Point`

Main package root:
- `src/webcrawler`

---

## 4. Module-Level Design

## 4.1 Entry & Orchestration

### `src/webcrawler/main.py`
Commands:
- `index`
- `search`
- `status`
- `web`
- `start` (user-friendly launcher)

Key behavior:
- Human-readable terminal outputs for `index/status/search`.
- Launcher mode:
  - picks available port
  - prints direct links
  - optional browser open
  - graceful Ctrl+C message

### `src/webcrawler/app.py`
Application composition root:
- Builds queue, scheduler, fetcher, storages, status service, crawler service, search service.
- Exposes:
  - `index(...)`
  - `run_search(...)`
  - `get_status()`
  - `list_crawled_pages(...)`
  - `list_crawl_runs()`

---

## 4.2 Crawler Engine

### `src/webcrawler/crawler/service.py`
Core crawler logic.

Responsibilities:
- Validate and normalize origin.
- Reset scheduler for new run.
- Bootstrapping:
  - from checkpoint if resume
  - otherwise seed origin
- Spawn a single worker task to preserve strict BFS visit order
- Enforce request throttling (`AsyncRateLimiter`)
- Fetch pages, emit events, index successful HTML pages
- Enqueue discovered links for next depth
- Save checkpoint continuously
- Stop based on queue idle + page limits

Event sink:
- Supports callback injection (`event_sink`) for manager-level persistence/logging.

Important counters:
- `_pages_crawled`, `_active_workers`, `_visit_order`, `_event_order`

### `src/webcrawler/crawler/fetcher.py`
HTTP fetch implementation:
- Uses `urllib.request.urlopen`
- Timeout via config
- `Accept-Encoding`: gzip/deflate/identity
- Handles decompression
- Processes only `text/html` or `application/xhtml+xml`
- Parses links and title from HTML
- Clips content to `max_content_chars`

### `src/webcrawler/crawler/rate_limiter.py`
`AsyncRateLimiter`:
- token interval style with monotonic time
- global serialization with async lock
- rate = requests/sec

### `src/webcrawler/crawler/storage.py`
In-memory runtime storage for crawl runs/pages:
- Start run / finish run
- Store `CrawledPage`
- List pages by run

---

## 4.3 Queue & Scheduling

### `src/webcrawler/queue/memory_queue.py`
Bounded FIFO queue:
- `enqueue` returns `False` if full (backpressure)
- `dequeue` pops from left
- thread-safe with lock

### `src/webcrawler/queue/scheduler.py`
Scheduler responsibilities:
- Enforce max depth
- Maintain seen URL set (dedup)
- Normalize URL before scheduling
- Roll back seen mark if enqueue fails (queue full)
- Restore seen + pending from checkpoint

BFS note:
- Queue is FIFO and consumed by a single worker.
- Visit order is strict BFS and deterministic for a given crawl state.

---

## 4.4 URL/HTML Utilities

### `src/webcrawler/utils/url.py`
Provides:
- `normalize_url(...)`
  - http/https only
  - lowercased scheme/host
  - default port strip (80/443)
  - fragment removed
  - trailing slash normalization
  - query param sort
- `extract_links_and_title(...)`
  - HTMLParser-based link/title extraction
  - deduplicates links per page
- `html_to_text(...)`
  - removes script/style blocks
  - normalizes whitespace
  - HTML unescape for readable snippets

### `src/webcrawler/utils/persistence.py`
Checkpoint persistence:
- JSON with:
  - seen URLs
  - pending queue entries (`url/depth/origin/parent_url`)

---

## 4.5 Indexing & Search

### `src/webcrawler/index/sqlite_storage.py`
SQLite-backed index.

Table: `documents`
- `url TEXT PRIMARY KEY`
- `title TEXT NOT NULL`
- `content TEXT NOT NULL`
- `crawl_run_id TEXT`
- `indexed_at TEXT NOT NULL`

Features:
- WAL mode enabled.
- Migration safety for older DBs without `crawl_run_id`.
- Upsert on URL conflict.
- Search filters:
  - keyword (title/content LIKE)
  - domain
  - crawl_run_id
  - indexed_from (date)
  - indexed_to (date)
- Snippet built from `html_to_text(content)`

### `src/webcrawler/search/service.py`
Thin abstraction over storage search.

---

## 4.6 Status

### `src/webcrawler/status/service.py`
Thread-safe in-process runtime stats:
- start/stop markers
- queue size
- seen count
- page started/indexed/failed
- active workers
- dropped by backpressure

---

## 4.7 Web Layer

### `src/webcrawler/web/manager.py`
Persistent crawler/job manager.

Persistent metadata DB:
- `data/crawler_meta.sqlite3`

Tables:
- `crawler_jobs`
- `crawler_events`

Manager responsibilities:
- create crawler jobs
- list/filter jobs
- retrieve crawler status + event log
- aggregate overview metrics
- delete crawler data
- clear all data
- load old jobs on startup
- mark old `running` jobs as `interrupted` at boot

Data persistence:
- Job definitions persisted in `crawler_jobs`
- Event stream persisted in `crawler_events`
- Crawl content persisted in `documents`
- Queue state persisted in checkpoint json

### `src/webcrawler/web/server.py`
HTTP server (`ThreadingHTTPServer`) with SSR HTML templates.

Routes:
- `GET /`, `GET /crawler/new`
- `POST /crawler/new`
- `GET /search`
- `GET /status`
- `POST /crawler/delete`
- `POST /data/clear`
- `GET /api/overview`
- `GET /api/status?id=<crawler_id>`

UI behavior:
- Global dark default theme
- Theme toggle persisted in localStorage
- No theme flicker (theme script in `<head>` before paint)
- KPI cards:
  - URLs Visited
  - Active Crawlers
  - Total Created
- New crawler form with operational knobs
- Search page with optional domain filter
- Status page:
  - crawler list
  - ID clickable to detail
  - delete action
  - clear-all action
  - detail section includes:
    - humanized timestamps
    - visited URL table
    - activity log table
- Footer signature: `Built by istemihan`

Caching:
- `Cache-Control: no-store` on HTML and JSON responses.

---

## 5. Data Model

## 5.1 Core dataclasses (`src/webcrawler/models.py`)
- `CrawlTask`
- `CrawlResult`
- `Document`
- `SearchHit`
- `CrawledPage`
- `CrawlRun`
- `CrawlerStats`

## 5.2 Event Payload Contract
Required keys:
- `event_order: int`
- `event_type: str`
- `url: str`
- `depth: int`

Optional fields in `info`:
- `source`
- `status_code`
- `error`
- `origin`
- `parent_url`

Humanized mappings:
- `queue_enqueue` -> Added to queue
- `queue_restored` -> Restored from checkpoint
- `queue_drop_backpressure` -> Dropped due to backpressure
- `visit_start` -> Visit started
- `visit_done` -> Visit completed
- `visit_error` -> Visit failed

---

## 6. Configuration

`src/webcrawler/config.py` / env-backed:
- `CRAWLER_ORIGIN_URL`
- `CRAWLER_MAX_DEPTH`
- `CRAWLER_MAX_PAGES`
- `CRAWLER_TIMEOUT`
- `CRAWLER_REQUESTS_PER_SECOND`
- `CRAWLER_WORKER_COUNT`
- `CRAWLER_QUEUE_CAPACITY`
- `CRAWLER_MAX_CONTENT_CHARS`
- `CRAWLER_USER_AGENT`
- `CRAWLER_INDEX_DB_PATH`
- `CRAWLER_CHECKPOINT_PATH`

Defaults:
- origin: `https://example.com`
- depth: `1`
- pages: `100`
- timeout: `8.0s`
- rps: `3.0`
- workers: `4`
- queue capacity: `2000`

---

## 7. User Flows

## 7.1 Create Crawler (Web)
1. User opens `/crawler/new`.
2. Enters origin + limits.
3. Submits form.
4. Server creates crawler ID and starts background crawl thread.
5. User redirected to `/status?id=<crawler_id>`.

## 7.2 Search
1. User opens `/search`.
2. Enters query (optional domain filter).
3. Results returned from SQLite index.

## 7.3 Status Monitoring
1. User opens `/status`.
2. Filters crawler list if needed.
3. Clicks crawler ID.
4. Views:
   - metadata timestamps
   - visited URLs
   - activity log

## 7.4 Delete Crawler
1. User clicks delete in row.
2. Manager removes:
   - crawler job row
   - crawler events
   - indexed docs related to crawler
   - checkpoint file

## 7.5 Clear All
1. User clicks clear-all.
2. If any crawler running -> blocked.
3. Else clears all jobs/events/docs/checkpoints.

---

## 8. CLI Contracts

## 8.1 `index`
Example:
```bash
PYTHONPATH=src python -m webcrawler.main index --origin https://example.com --max-depth 1 --max-pages 20 --no-resume
```
Output: readable status block.

## 8.2 `search`
Example:
```bash
PYTHONPATH=src python -m webcrawler.main search --query robotics --domain wikipedia.org --limit 10
```
Output: numbered result list with title/url/snippet.

## 8.3 `status`
Example:
```bash
PYTHONPATH=src python -m webcrawler.main status
```

## 8.4 `web`
Example:
```bash
PYTHONPATH=src python -m webcrawler.main web --host 127.0.0.1 --port 8080
```

## 8.5 `start` (Launcher)
Example:
```bash
./run.sh
./run.sh --open-browser
./run.sh --port 8090
```
Behavior:
- auto free-port fallback
- prints page links
- graceful shutdown message on Ctrl+C

---

## 9. Error Handling Requirements

- Invalid origin URL -> explicit validation error.
- Network failure -> emit `visit_error`, continue crawl.
- Non-HTML content -> skip with `unsupported-content-type`.
- Queue full -> drop enqueue and emit backpressure event.
- Running crawler delete/clear -> operation blocked where required.
- Missing crawler ID in status -> return not-found behavior.

---

## 10. Security and Safety Baselines

- Restrict crawl schemes to `http/https`.
- Avoid executing page scripts; parse static HTML only.
- Escape all rendered UI text (`html.escape`) to prevent XSS via indexed content.
- Use parameterized SQLite statements.

---

## 11. Performance Characteristics

- Concurrent worker fetches + global rate limit.
- WAL mode for SQLite to support concurrent reads/writes better.
- Content clipping (`max_content_chars`) for bounded memory/storage.
- Bounded queue for backpressure safety.

---

## 12. Observability

Runtime/UI observability:
- KPI cards
- crawler state list
- visited URLs table
- activity logs per crawler
- timestamps for lifecycle milestones

Terminal observability:
- structured status block
- formatted search output
- launcher lifecycle prints

---

## 13. Known Design Notes / Caveats

- Queue strategy is FIFO (BFS intent), but multi-worker execution can interleave completion order.
- Pause/resume controls are not active in UI (resume-from-checkpoint path exists at manager level but not user-exposed currently).
- Search ranking is basic LIKE matching, recency-ordered.
- Domain filter is substring match on URL.

---

## 14. Testing Strategy

## 14.1 Unit/Component
- URL normalization edge cases.
- Queue capacity and enqueue-drop behavior.
- Scheduler dedup + max depth rules.
- HTML parsing and visible text extraction.
- SQLite index CRUD and filtered search.
- Event sink persistence behavior.

## 14.2 Integration
- Crawl small local site fixture (depth control + dedup).
- Verify index/search after crawl.
- Verify persisted metadata and logs survive restart.
- Verify delete crawler and clear all affect:
  - jobs/events/checkpoints
  - indexed documents

## 14.3 Manual UI QA
- Theme defaults to dark.
- Theme toggle persists and no flicker on reload.
- ID links open status detail.
- Search UX without highlight.
- Status tables scroll properly.

---

## 15. Acceptance Criteria (Build-Complete)

1. User can launch with `./run.sh` and see working web UI.
2. Crawler creation works and crawls with depth/max pages constraints.
3. Duplicate normalized URLs are not re-crawled within run.
4. Index is persisted and searchable across process restarts.
5. Crawler jobs and event logs persist across process restarts.
6. Status page shows crawler list + detail + visited URLs + activity log.
7. Delete crawler removes associated data and updates UI metrics.
8. Clear-all removes all persisted crawler/index/checkpoint data when safe.
9. Terminal UX is human-readable (not raw JSON default).
10. Theme behavior: dark default, no refresh flicker, dynamic button text.

---

## 16. Repository Map (Implementation Pointers)

- Entry:
  - `src/webcrawler/main.py`
  - `run.sh`
- App wiring:
  - `src/webcrawler/app.py`
- Crawl core:
  - `src/webcrawler/crawler/service.py`
  - `src/webcrawler/crawler/fetcher.py`
  - `src/webcrawler/crawler/rate_limiter.py`
  - `src/webcrawler/crawler/storage.py`
- Queue:
  - `src/webcrawler/queue/memory_queue.py`
  - `src/webcrawler/queue/scheduler.py`
- Index/search:
  - `src/webcrawler/index/sqlite_storage.py`
  - `src/webcrawler/search/service.py`
- Status:
  - `src/webcrawler/status/service.py`
- Web:
  - `src/webcrawler/web/server.py`
  - `src/webcrawler/web/manager.py`
- Utilities:
  - `src/webcrawler/utils/url.py`
  - `src/webcrawler/utils/persistence.py`
  - `src/webcrawler/utils/logger.py`

---

## 17. Operational Runbook

### Start product
```bash
cd /home/istemihan/Desktop/codex
./run.sh
```

### Start with browser
```bash
./run.sh --open-browser
```

### Start on custom port
```bash
./run.sh --port 8090
```

### CLI crawl
```bash
PYTHONPATH=src python -m webcrawler.main index --origin https://example.com --max-depth 1 --max-pages 20 --no-resume
```

### CLI search
```bash
PYTHONPATH=src python -m webcrawler.main search --query ai --domain wikipedia.org --limit 20
```

### CLI status
```bash
PYTHONPATH=src python -m webcrawler.main status
```

---

## 18. Future Roadmap (Post-Current PRD)

- Better ranking (BM25 or hybrid).
- robots.txt + crawl-delay policy control.
- Domain allow/deny lists.
- Real-time progress channel (SSE/WebSocket).
- Export/import crawl snapshots.
- Pause/resume/stop controls for active jobs.
- Auth for UI in multi-user environment.
