# Recommendation for Production Next Steps

If we take this project to production, I’d do it in two practical phases. First, keep the current architecture but make it operationally safe: run crawler jobs in a managed process (systemd/container), add basic dashboards (crawl rate, queue growth, error rate), and set hard limits per domain so one bad target cannot flood the system. This gives us a setup the team can run every day without babysitting.

THen, improve quality where users feel it most: faster search and better result relevance. I’d add a proper ranking layer (BM25 or similar), keep incremental indexing always on, and split crawler/search into separate deployable services when traffic grows. At that point we can move from SQLite to a dedicated search stack (Postgres FTS/OpenSearch) with minimal product changes, because the current boundaries are already clean.
