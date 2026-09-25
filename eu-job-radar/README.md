# EU Job Radar

A local tool for finding, screening and tracking software, SDE, ML and AI
engineering jobs in the EU and UK. It is the sibling of
[eu-phd-radar](https://github.com/Abdulla4akash/eu-phd-radar) and follows its
rules: it reads public sources only when you ask, keeps a dated history of
every change it sees, keeps what it collects apart from what you decide, and
never sends email or submits applications.

Status: **Live boards validated; deadline verification partial (2026-09-25).** Live collection from company job boards through the
public APIs of three applicant tracking systems (Greenhouse, Lever and
Ashby), a rules-only screen against your preferences, a discovery inbox, an
application queue with tasks, next steps and deadlines, a registry of
internship and graduate windows at the largest employers, CSV/JSON export
and an offline demo. Nothing is scheduled. See [the roadmap](docs/roadmap.md) for what
follows and [docs/sources.md](docs/sources.md) for what is known about each
API.

## Deadline first

**Google internship deadline:** The checked 2027 **EMEA SWE/SRE internships**
say **before 23 October 2026**, without a time or timezone. The internship
associated with the reported **7 October** date remains unidentified; the
23 October date does not apply to every Google internship.

| Programme | Confirmed event | Evidence checked 2026-09-25 |
|---|---|---|
| Google, SWE/SRE BS/MS Intern (EMEA), 2027 | **Apply before 23 Oct 2026** | “Please complete your application before 23rd October 2026.” [Official London posting](https://www.google.com/about/careers/applications/jobs/results/100028133205254854-software-engineering-site-reliability-engineering-bsms-intern-2027); [other checked postings](docs/sources.md#deadline-mismatch-google). Time/timezone unstated. |
| Booking.com, Software Engineer Graduate (Amsterdam), 2027 | **Opens 4 Jan 2027**; closing unknown | “Applications will open on January 4th, 2027.” [Official page](https://careers.booking.com/early-careers/). Time/timezone unstated. |
| Other 20 registry programmes | unknown | No exact 2027 dates verified. [Page results and access limits](docs/sources.md#all-22-registry-programmes). |

The same Booking.com page gives **12 Oct 2026** as the opening for its July
2027 Amsterdam **software engineering internships**. That separate programme
is not the registry's graduate window. No closing date was found for it.

`job-radar deadlines` lists every dated deadline, soonest first, with its
basis. Check the official page before relying on a reported date.

## Top companies: hiring windows

Google, Meta, Amazon, Microsoft, Apple, Bloomberg, Palantir, Stripe and the
trading firms (Jane Street, Optiver, IMC, Citadel, HRT, Jump, G-Research,
XTX) hire students through annual programmes or rolling postings, so the radar tracks their **internship and graduate windows**
(`src/eu_job_radar/data/windows.toml`) the way eu-phd-radar tracks ELLIS
and IMPRS. Each opening and closing date carries a basis: *announced*
(read on the official page, with the wording quoted), *reported by the
owner*, *reported by you*, or *unknown*. Only an announcement counts as a
fixed deadline, and a "usually opens in autumn" pattern never becomes a
date.

```sh
job-radar windows                                   # all programmes and what is known
job-radar windows google-swe-intern                 # page, dates, basis, usual pattern
job-radar windows-report jane-street-intern --closes 2026-11-01 \
    --source https://www.janestreet.com/join-jane-street/
job-radar windows-track google-swe-intern           # into your queue, deadline included
job-radar deadlines --days 45                       # soonest first
job-radar windows-check                             # re-read pages; compares quoted wording only
job-radar app-deadline a-9f8e7d 2026-10-30          # a deadline for any application
```

## Why company boards

Most AI labs, scale-ups and many trading firms publish every open job
through their ATS, and Greenhouse, Lever and Ashby each offer a public,
keyless API for a company's published jobs. One request reads a company's
whole board, so a complete read can also tell you when a job disappears.
Big job sites (LinkedIn, Indeed, Glassdoor) are not read: their terms forbid
automated collection.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). No runtime
dependencies.

```sh
git clone https://github.com/Abdulla4akash/test.git
cd test/eu-job-radar
uv sync
uv run job-radar --help
uv tool install --force .   # optional: a `job-radar` command on your PATH
```

## Try the demo first

```sh
uv run job-radar demo
```

The demo creates a temporary workspace, collects three **synthetic**
company boards (fictional companies on example.org, in the real API shapes)
twice through the real HTTP layer on an offline network, screens them,
promotes and dismisses postings, records an application, and checks that the
second collection (a withdrawn job, a renamed one, a new one and a failed
board) leaves everything you recorded intact. It prints PASS or FAIL per
check and the commands for exploring the result.

## Use it

```sh
job-radar init                      # workspace in ~/.local/share/eu-job-radar/workspace
$EDITOR ~/.local/share/eu-job-radar/workspace/preferences.toml
job-radar validate
job-radar boards                    # 33 verified company boards, with collection health
job-radar collect --all             # one request per board, plus robots.txt per API host
job-radar inbox --view new          # first seen in the last 7 days
job-radar inbox --view check        # unresolved role/location checks
job-radar inbox --country DE --family ml --details
job-radar show p-1a2b3c4
job-radar promote p-1a2b3c4 --note "strong team"
job-radar dismiss p-5d6e7f8 --note "needs German"
job-radar queue
```

Track an application (you apply yourself; the tool only records it):

```sh
job-radar app-status a-9f8e7d applied --on 2026-10-12
job-radar app-next a-9f8e7d "Recruiter call" --due 2026-10-20
job-radar task-add a-9f8e7d "Revise CV for ML infra" --kind cv --due 2026-10-15
job-radar task-done 3
job-radar app-add --company "Referral Co" --title "ML Engineer" --url https://example.org/job
job-radar export                    # CSV; --format json for everything
```

Add a company the registry lacks. Open one of its jobs; if the address is on
`boards.greenhouse.io`, `job-boards.greenhouse.io`, `jobs.lever.co`,
`jobs.eu.lever.co` or `jobs.ashbyhq.com`, that is its board:

```sh
job-radar boards-add --company "Acme AI" --url https://jobs.ashbyhq.com/acme
job-radar collect acme-ai
job-radar boards-disable some-board --note "token wrong"
```

## The screen

Every posting is sorted into one of three bins, with its reasons:

| Mark | Bin | Meaning |
|---|---|---|
| ✓ | relevant | role family, level and location all match your preferences |
| ? | check | nothing rules it out, but something could not be read (for example "Remote" with no region) |
| ✗ | outside | at least one preference rules it out (`inbox --view outside` lists them, with reasons) |

It reads the **title** for the role family (SWE, SDE, backend, ML, AI/LLM,
research engineer, data, infra/SRE, quant developer, and more) and the level
(intern, graduate/junior, not stated, senior, staff, manager), and the
**location text** for countries (EU, EEA, Switzerland and the UK by default)
and remote regions. There is no score and no guessing: an unreadable
location is never treated as a match. Preferences are re-read on every
command, so editing them changes the inbox without re-collecting.

## What collection does and does not do

- One request per board, through a shared HTTP layer taken from
  eu-phd-radar: only public https addresses on registered API hosts,
  robots.txt checked first, one request per second per API, an identifiable
  user-agent, a run request budget (`--budget`, default 80), bounded retries,
  conditional requests and a 20 MB board response limit (5 MB for page checks). Every request is logged
  (`job-radar runs N --requests`).
- A job not seen in a **complete** read of its board is marked
  `no longer listed`; it is never deleted. A partial read, a failure or a
  404 never marks anything. An application linked to a withdrawn posting
  keeps its status and the queue warns you.
- Collection cannot write your applications, tasks or decisions: the
  database refuses it (an SQLite authorizer), and a test checks that.
- The **33 retained boards were verified on 2026-09-25**. Plaid and Mistral
  moved to Ashby; DeepMind moved to Google Careers and was removed from ATS
  collection. Verification is dated evidence; a future failure still shows
  in `job-radar boards`. [Full validation and request ledger](docs/sources.md).

## Privacy

The workspace (database, preferences, HTTP cache and exports) lives outside
the repository. Workspace content is never uploaded. Lever and Ashby bundle
descriptions in their board responses; these can remain in the local HTTP
cache for two days, but are discarded from normalized postings. Only titles,
locations, links and a few labels are stored in the posting tables.

## Development

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests never touch the network (`tests/conftest.py` blocks the transport,
DNS and sockets) and use synthetic data only. See [AGENTS.md](AGENTS.md).
