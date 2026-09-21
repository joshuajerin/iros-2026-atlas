# IROS 2026 Atlas

IROS 2026 Atlas is an independent, account-free research map for the 1,933-paper IROS 2026 catalog. It is not an official IROS site. It preserves the official catalog as the source of truth, provides a visual keyword constellation and search experience, and exposes the same read-only research surface to agents through REST and MCP.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m iros_catalog --db data/iros.sqlite atlas-build
cd web && npm install && npm run build && cd ..
.venv/bin/python -m iros_catalog --db data/iros.sqlite serve --port 8080
```

Open `http://127.0.0.1:8080`. For frontend iteration, run `npm run dev` inside `web/`; Vite proxies API and MCP traffic to port 8080.

## Product surface

- `/` — keyword constellation, conference signals, topic and presence summaries, and the full paper explorer at the bottom.
- `/papers` and `/papers/:number` — URL-backed search, filters, paper evidence, schedule metadata, and related work.
- `/rankings`, `/topics/:slug`, `/institutions`, `/researchers` — IROS 2026-specific score views.
- `/connect` — Streamable HTTP MCP connection details and tool inventory.

The constellation uses normalized official keywords as nodes and co-occurrence as links. Clicking a node sends its normalized keyword into the paper explorer URL state. A text-first table/list presentation remains available through the explorer and route pages; reduced-motion settings disable transitions.

## Data and score integrity

`data/iros.sqlite` is a generated deployment artifact and is intentionally excluded from Git. The source pipeline preserves raw official values while Atlas derives normalized `atlas_*` tables for keywords, paper-keywords, institution presence, paper-institutions, scores, and metadata.

Atlas Score version `2026.1` uses the documented weights: 30% official recognition, 30% citation velocity, 20% influential citations, 15% reproducibility, and 5% cross-provider confirmation. The current catalog has no imported exact citation or influential-citation records, so those components use a transparent neutral prior rather than a fabricated zero or a fuzzy match. Score responses return every component, confidence, eligibility, formula version, computation timestamp, and source references.

Only high-confidence records are included in ranked leaderboards; all records remain searchable. Institution results are **IROS 2026 research presence**, never university prestige. Researcher results are source-name profiles until a verified external ID or high-confidence name-plus-affiliation match is imported; ambiguous identities stay separate.

The included keyword tracking means current conference frequency, co-occurrence, topic prominence, and schedule distribution. It does not claim year-over-year momentum.

## REST API

OpenAPI is available at `/docs` when the server runs.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/overview` | Dataset stamp, conference signals, topics, and top papers |
| `GET /api/v1/papers` and `/papers/{number}` | Searchable paper summaries and full evidence-backed detail |
| `GET /api/v1/rankings/{papers|institutions|researchers}` | Eligibility-aware rankings |
| `GET /api/v1/keywords/network` | Lazy keyword graph payload |
| `GET /api/v1/topics/{slug}` | Topic keywords and ranked papers |
| `GET /api/v1/institutions/{id}` | Conference-presence profile |
| `GET /api/v1/researchers/{id}` | Source-name researcher profile |
| `GET /api/v1/methodology/atlas-score` | Formula, weights, timestamp, and safeguards |

Pagination limits, search tokens, and result sizes are capped. There is no arbitrary SQL, write endpoint, or provider request during a public query.

## Agent connector

The hosted connector endpoint is:

```text
https://YOUR-ATLAS-HOST/mcp/
```

It uses the MCP Streamable HTTP transport and exposes read-only tools: `search_papers`, `get_paper`, `rank_papers`, `explore_keyword`, `compare_topics`, `get_institution`, `get_researcher`, `discover_related_papers`, and `build_reading_list`. Resources include `iros://paper/{number}`, `iros://topic/{slug}`, `iros://rankings/overall`, and `iros://methodology/atlas-score`.

Set `IROS_ATLAS_ALLOWED_HOSTS` and `IROS_ATLAS_ALLOWED_ORIGINS` to comma-separated production values at deployment; the local defaults only permit localhost. Requests are body-size capped and rate-limited by anonymous IP. For private/offline research, the same SQLite snapshot can later be paired with a stdio MCP wrapper; clients without MCP can use OpenAPI directly.

## Ingestion and release

Run the existing official sync and conservative enrichment steps, then rebuild Atlas:

```bash
.venv/bin/python -m iros_catalog --db data/iros.sqlite sync
.venv/bin/python -m iros_catalog --db data/iros.sqlite sync-official-details
.venv/bin/python -m iros_catalog --db data/iros.sqlite enrich --limit 50
.venv/bin/python -m iros_catalog --db data/iros.sqlite atlas-build
```

Provider enrichment must attach external metrics only after an exact identifier/title match; never run it in API requests. A production scheduled job should build a new snapshot, validate paper coverage and score determinism, then atomically promote it.

## Verify

```bash
.venv/bin/python -m pytest -q
cd web && npm run build
docker build -t iros-2026-atlas .
```
