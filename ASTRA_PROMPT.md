Check and fix `eu-job-radar`, a Python command-line tool for finding, screening and tracking SWE, SDE, ML and AI jobs and internship deadlines in the EU and UK. It was written without internet access, so nothing in it has been checked against a live site. Your job is to check it live, fix what is wrong and report what you did.

## Where it is
- Repository: https://github.com/Abdulla4akash/test, branch `main`, folder `eu-job-radar/`. Leave the repo's other files alone (the heart-disease CSVs, `job-tracker/`, `README.md`).
- Read `eu-job-radar/AGENTS.md`, `README.md`, `docs/sources.md` and `docs/roadmap.md` first. AGENTS.md has the rules, and they are binding.
- It is modelled on https://github.com/Abdulla4akash/eu-phd-radar. `fetch.py` and `robots.py` were copied from that repo. Do not edit eu-phd-radar.

## Setup
```sh
cd eu-job-radar
uv sync
uv run pytest -q          # 102 should pass
uv run ruff check . && uv run ruff format --check .
uv run job-radar demo     # 21/21 should pass
```
If any of these fail, fix them first.

## Work, in order

1. **Deadlines (most urgent).** The Google internship deadline of 7 Oct 2026 is recorded as `owner_reported` in `src/eu_job_radar/data/windows.toml`. It has not been checked.
   - Open Google's official students/careers page. Find which internship(s) close on 7 October 2026, plus the time and timezone.
   - If the page confirms it, change the claim to `basis = "current_cycle_announcement"` and add the exact quoted `wording`, the page `source` and the date you checked. If the page says something different, record what it says and flag the mismatch at the top of your report.
   - Do the same for the other 21 programmes (Meta, Amazon, Microsoft, Apple, DeepMind, Bloomberg, Palantir, Stripe, Jane Street, Optiver, IMC, Citadel, HRT, Jump, G-Research, XTX, Booking.com, Arm, ASML, SAP). Record a date only when the official page states it for the 2027 cycle. Otherwise leave it `not_yet_verified`. Never copy a past year's date, and never turn a "usually opens in autumn" pattern into a date.
   - Fix any programme `page` URL that is wrong or redirects.

2. **Live collection check.** Use a validation workspace outside the repo (`uv run job-radar --workspace ~/job-radar-validation init --validation`). Run `collect --all --budget 80` once and count every request.
   - For each of the 34 boards in `src/eu_job_radar/data/boards.toml`, look at `job-radar boards`. Where a token returns `not_found`, find the company's real Greenhouse/Lever/Ashby board from its careers page and fix the token, or remove the board if the company doesn't use one of those systems. Set `token_status = "verified"` only for tokens that returned a readable board, and note the date.
   - Compare the real JSON from each API (Greenhouse `boards-api.greenhouse.io/v1/boards/{token}/jobs`, Lever `api.lever.co/v0/postings/{token}?mode=json`, Ashby `api.ashbyhq.com/posting-api/job-board/{token}`) with what the adapters in `src/eu_job_radar/adapters/` expect. Fix any mismatch. Add tests using synthetic payloads in the real shape: fictional companies, example.org links, never captured real postings.
   - Check robots.txt on the three API hosts and record the results in `docs/sources.md` with the date.

3. **Screening quality.** Run `inbox`, `inbox --view outside` and `inbox --view check` on the real data. Look for wrong results: engineering roles ruled out, non-engineering roles let through, locations misread (for example Cambridge UK vs Cambridge MA, "Remote - EMEA", multi-city lists). Fix the rules in `screening.py` and `locations.py`, and add a test for each fix. Keep it rules-only with stated reasons: no scores, and never treat an unreadable location as a match.

4. **General review.** Read the whole package for bugs, especially:
   - `ingest.py`: missing-posting logic, where only a complete read may mark a posting missing.
   - `db.py`: the v1→v2 migration and the `collected_state_only` guard.
   - `windows.py` and `cli.py`: edge cases.

   Fix real bugs, with a test for each.

5. **Docs.** Update `docs/sources.md` (every check with URL, date and result), `docs/roadmap.md` (mark Chunk 2 done if it is), and the "Deadline first" table at the top of `README.md` with the confirmed dates.

## Rules
- Make at most one request per second per site, send an honest user-agent (the tool's built-in one), and respect robots.txt.
- Do not scrape LinkedIn, Indeed, Glassdoor or any site whose terms or robots.txt forbid it.
- Do not apply to jobs, contact anyone or set up scheduled collection.
- Never commit databases, caches, real application data, captured job postings or secrets. Tests stay offline and synthetic.
- Before each commit, run `uv run pytest -q`, `uv run ruff check .` and `uv run ruff format --check .`, and make sure all three pass.
- Commit in small, clearly described commits and push to `main` of Abdulla4akash/test.

## Report back
1. Deadline mismatches or confirmations, first, with a quote and URL for each.
2. Board tokens that were fixed, removed or verified.
3. Bugs fixed, and the tests added for them.
4. Anything you couldn't check, and why.
5. The number of live requests made.
