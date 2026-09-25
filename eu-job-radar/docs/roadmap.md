# Roadmap

## Chunk 1 (done)

- Greenhouse, Lever and Ashby adapters through eu-phd-radar's HTTP layer.
- 33 shipped company boards (verified 2026-09-25) and user-added boards.
- Postings with snapshots and automated history; missing only after a
  complete read; collection barred from user-owned tables.
- Rules-only screen (role family, level, countries, remote regions) from
  `preferences.toml`, with reasons.
- Inbox views, promote / dismiss / later / undo, application queue with
  status, next step, tasks and notes; CSV/JSON export.
- Offline demo with PASS/FAIL checks; 174 offline tests.

## Hiring windows (done)

- 22 internship and graduate programmes at Google, Google DeepMind, Meta,
  Amazon, Microsoft, Apple, Bloomberg, Palantir, Stripe, Booking.com, Arm,
  ASML, SAP and eight trading firms, with dated claims and a basis per
  claim; schema v2 with a migration; `deadlines`, `windows-report`,
  `windows-track`, `windows-check` and `app-deadline`.

## Chunk 2: first live validation (partially complete)

Completed on **2026-09-25**, with [dated evidence and request accounting](sources.md):

- Exactly one validation `collect --all --budget 80`: 37 requests. A targeted
  correction run used 5 more; official-page/docs/robots research used 100.
- All 33 retained boards verified. Plaid and Mistral moved to Ashby;
  DeepMind's obsolete Greenhouse board was removed because its careers page
  now directs candidates to Google Careers.
- Live response shapes and all three API robots responses recorded. Revised
  adapters replayed all 33 cached payloads successfully without new requests.
- Large-board response failures fixed with a finite 20 MB collection bound;
  malformed/duplicate inventory, nested database guard and window/CLI bugs
  covered by offline synthetic regressions. Populated v1 migration checked.
- All inbox views run on real data. Three 60-item title/location samples
  reviewed, observed error proportions recorded and rules fixed.
- Google deadline mismatch resolved: checked 2027 SWE/SRE postings say
  **before 23 October 2026**, not 7 October; no time/timezone stated.
  Booking.com Amsterdam SWE graduates **open 4 January 2027**.
- All 22 programme pages checked or attempted, redirects corrected where
  evidence was available; no exact date invented for an unknown window.
- 174 offline tests, lint, formatting and 21/21 demo checks pass.

**Still open:** complete official-page deadline coverage. Microsoft/Apple
robots were unusable; Bloomberg's destination disallows automated access;
Citadel, Citadel Securities, HRT and SAP refused access. Other programme
pages give rolling/generic information, an older cycle or no exact 2027
dates. These claims stay `not_yet_verified`. A manual owner-provided official
announcement or a later permitted check can resolve them. Chunk 2 is not
marked fully done while those original deadline acceptance checks remain
unresolved. No repeated or scheduled collection was configured.

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
