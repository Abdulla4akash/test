# Roadmap

## Chunk 1 (done)

- Greenhouse, Lever and Ashby adapters through eu-phd-radar's HTTP layer.
- 34 shipped company boards (tokens unverified) and user-added boards.
- Postings with snapshots and automated history; missing only after a
  complete read; collection barred from user-owned tables.
- Rules-only screen (role family, level, countries, remote regions) from
  `preferences.toml`, with reasons.
- Inbox views, promote / dismiss / later / undo, application queue with
  status, next step, tasks and notes; CSV/JSON export.
- Offline demo with PASS/FAIL checks; 93 offline tests.

## Chunk 2: first live validation

Acceptance: in a validation workspace (`job-radar init --validation`), a
`collect --all` within a stated budget; each shipped token confirmed or
corrected; docs/sources.md updated with dated results, including the real
response shapes and robots.txt of the three API hosts; screening
false-positive and false-negative rates noted on the real titles and
locations, with rule fixes.

## Chunk 3: more sources

Each only after its own terms, robots.txt and API check: Workable, Personio,
SmartRecruiters, Recruitee, Teamtailor, Workday (per-company JSON endpoints),
and boards with public feeds (for example Arbeitnow's API, EuroTechJobs RSS).
Cross-board duplicate detection (the same job on a company's ATS and a
board) as match candidates the user confirms, following eu-phd-radar.

## Chunk 4: better decisions

Visa-sponsorship evidence per company (UK licensed sponsor register, Dutch
IND recognised sponsors), language requirements read from the title, salary
where the API gives it, an HTML report, and a shared "EU/UK tech job"
section in the eu-phd-radar report.

## Open decisions for the owner

- Whether to fetch job descriptions (more accurate screening, but more
  requests and stored third-party text).
- Default levels: should `senior` stay in by default?
- Whether jobs and PhDs should share one queue later.
