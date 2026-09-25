# Sources

Written 2026-09-25. **Nothing on this page was re-checked live**: the
environment that wrote Chunk 1 had no internet access (its proxy refused
every outbound connection). The API descriptions come from the tool
author's knowledge of each vendor's public documentation; the adapters parse
defensively and report an unexpected shape as `parse_failed`, never as an
empty board. The first live run should confirm each row and update this page.

| ATS | Endpoint read | Docs | Auth | Status |
|---|---|---|---|---|
| Greenhouse | `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs` | Job Board API (developers.greenhouse.io/job-board.html) | none for published jobs | **unverified** |
| Lever | `GET https://api.lever.co/v0/postings/{token}?mode=json` (EU accounts: `api.eu.lever.co`) | Postings API (github.com/lever/postings-api) | none for published postings | **unverified** |
| Ashby | `GET https://api.ashbyhq.com/posting-api/job-board/{token}` | Public job posting API (developers.ashbyhq.com) | none | **unverified** |

What each adapter relies on:

- **Greenhouse**: `jobs[].id`, `title`, `location.name`, `absolute_url`,
  `updated_at`; `meta.total` when present (a shorter list is `partial`).
  Descriptions (`content=true`) are not requested.
- **Lever**: a JSON list; `id`, `text`, `hostedUrl`, `categories.location`,
  `categories.allLocations`, `categories.commitment`, `workplaceType`,
  `createdAt` (ms). An object with `ok: false` is a failure.
- **Ashby**: `jobs[]` with `id`, `title`, `location`, `secondaryLocations`,
  `isRemote`, `isListed`, `employmentType`, `publishedAt`, `jobUrl`.
  Unlisted jobs are skipped.

## Not read, on purpose

| Source | Why |
|---|---|
| LinkedIn, Indeed, Glassdoor | Terms of use forbid automated collection. Use them by hand. |
| Workday, SuccessFactors, iCIMS, Personio, SmartRecruiters, Workable | Not implemented yet; each needs its own access check first (see the roadmap). |
| Company careers pages (HTML) | Fragile and often disallowed; an ATS API is used instead where one exists. |

## Board tokens

The 34 shipped boards in `src/eu_job_radar/data/boards.toml` are all
`unverified`. A wrong token returns HTTP 404 and shows as `not_found` in
`job-radar boards`; nothing else happens. After a live run, mark the tokens
that returned a readable board as `verified` and note the date here.

## Hiring windows

`src/eu_job_radar/data/windows.toml` lists 22 programmes with their official
pages. None was read (no internet on 2026-09-25), so every date is either
`not_yet_verified` or, for Google's Software Engineering Intern (EMEA),
`owner_reported` 2026-10-07 from the owner's message of 2026-09-25. Upgrade a
claim to `current_cycle_announcement` only with wording quoted from the
page and the date you read it.
