# Agent instructions

This repository holds `eu-job-radar`, a local tool for software, SDE, ML and
AI engineering jobs in the EU and UK, with a collected-state store, a
rules-only screen and an application queue. It is the sibling of
[eu-phd-radar](https://github.com/Abdulla4akash/eu-phd-radar) and keeps its
rules. **Chunk 1 is implemented** (see [docs/roadmap.md](docs/roadmap.md)).

- [README.md](README.md): install, demo, commands.
- [docs/sources.md](docs/sources.md): the ATS APIs, what is known about each, and what is unverified.
- [docs/roadmap.md](docs/roadmap.md): what follows, in order.
- `src/eu_job_radar/data/boards.toml`: shipped company boards, each with an honest `token_status`.

## Rules

- Keep the network rules: every request goes through `fetch.py` (public
  https hosts only, registered hosts per adapter, robots.txt first, at most
  one request per second per API, an honest user-agent, request budgets).
  Adapters never open connections themselves.
- Read only public, documented job-board APIs. Do not scrape LinkedIn,
  Indeed, Glassdoor or any site whose terms or robots.txt forbid it, and do
  not bypass authentication, bot protection or a robots rule.
- No scheduled collection, no automatic applications, and no automatic
  messages to recruiters or companies. Recording `applied` is the user's
  statement.
- Collection must never write user-owned tables (applications, tasks,
  triage, local_boards, board_settings). Keep ingestion under
  `db.collected_state_only`.
- Only a complete read of a board may mark postings missing, and missing is
  never deleted.
- The screen stays rules-only with stated reasons: no combined score, no
  guessing a location, no treating unreadable data as a match.
- A board token becomes `verified` in `boards.toml` only after a live fetch,
  recorded with the date in docs/sources.md.
- Tests and the demo never use the live network and use synthetic data only
  (fictional companies, example.org links, real API host names where the
  code depends on them). Never commit real application records, captured
  job postings, databases, caches or personal documents.
- Schema changes need a migration in `db.py` and a `SCHEMA_VERSION` bump.

## Working on the code

Python 3.11+, `uv`, `ruff`, `pytest`. Run `uv run pytest -q`,
`uv run ruff check .` and `uv run ruff format --check .` before handing work
back. `fetch.py` and `robots.py` are copied from eu-phd-radar with only the
tool's name changed; port fixes between the two.
