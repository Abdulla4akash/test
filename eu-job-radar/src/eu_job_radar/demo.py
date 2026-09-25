"""`job-radar demo`: the whole workflow on synthetic data, offline.

Three fictional companies (on example.org) publish boards through the real
Greenhouse, Lever and Ashby API shapes. The demo collects them twice through
the real HTTP layer on a scripted offline network, screens the inbox,
promotes and dismisses postings, records an application, and checks that a
second collection (a withdrawn posting, a renamed one, a new one and a failed
board) never touches what the user recorded. It prints PASS or FAIL for
each check and never touches your real workspace.
"""

import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import applications, collect, views, workspace
from .clock import FixedClock
from .offline import OfflineNet

START = datetime.fromisoformat("2026-10-05T09:00:00+01:00")

GH_URL = "https://boards-api.greenhouse.io/v1/boards/example-northwind/jobs"
LEVER_URL = "https://api.eu.lever.co/v0/postings/example-fjord?mode=json"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/example-kestrel"


def _gh(job_id, title, location, updated="2026-10-01T10:00:00-04:00"):
    return {"id": job_id, "title": title, "location": {"name": location}, "updated_at": updated,
            "absolute_url": f"https://northwind.example.org/careers/{job_id}"}  # fmt: skip


def _lever(pid, title, location, workplace="hybrid", commitment="Full-time"):
    return {"id": pid, "text": title, "hostedUrl": f"https://fjord.example.org/jobs/{pid}",
            "categories": {"location": location, "team": "Engineering", "commitment": commitment},
            "workplaceType": workplace, "createdAt": 1790000000000}  # fmt: skip


def _ashby(pid, title, location, remote=False, secondary=()):
    return {"id": pid, "title": title, "location": location, "isRemote": remote, "isListed": True,
            "secondaryLocations": [{"location": s} for s in secondary],
            "department": "Research", "employmentType": "FullTime",
            "publishedAt": "2026-09-28T08:00:00.000+00:00",
            "jobUrl": f"https://kestrel.example.org/jobs/{pid}"}  # fmt: skip


def first_round(net: OfflineNet) -> None:
    net.json(GH_URL, {"jobs": [
        _gh(9001, "Machine Learning Engineer, Ranking", "Amsterdam, Netherlands"),
        _gh(9002, "Senior Backend Engineer (Go)", "London, UK"),
        _gh(9003, "Account Executive, DACH", "Munich, Germany"),
        _gh(9004, "Software Engineer, Payments", "New York, NY"),
        _gh(9005, "Staff Infrastructure Engineer", "Dublin, Ireland"),
    ], "meta": {"total": 5}})  # fmt: skip
    net.json(LEVER_URL, [
        _lever("fj-101", "Graduate Software Engineer", "Stockholm"),
        _lever("fj-102", "Data Engineer", "Remote", workplace="remote"),
        _lever("fj-103", "Quantitative Developer", "Copenhagen, Denmark"),
    ])  # fmt: skip
    net.json(ASHBY_URL, {"jobs": [
        _ashby("ks-1", "Research Engineer, Foundation Models", "Zürich", secondary=["Paris"]),
        _ashby("ks-2", "AI Engineer (LLM agents)", "Remote - Europe", remote=True),
        _ashby("ks-3", "Product Designer", "Berlin"),
    ]})  # fmt: skip


def second_round(net: OfflineNet) -> None:
    net.json(GH_URL, {"jobs": [
        # 9001 was withdrawn; 9002 was renamed; 9006 is new.
        _gh(9002, "Senior Backend Engineer (Go, Payments)", "London, UK", "2026-10-06T10:00:00Z"),
        _gh(9003, "Account Executive, DACH", "Munich, Germany"),
        _gh(9004, "Software Engineer, Payments", "New York, NY"),
        _gh(9005, "Staff Infrastructure Engineer", "Dublin, Ireland"),
        _gh(9006, "Junior ML Engineer, Perception", "Berlin, Germany"),
    ], "meta": {"total": 5}})  # fmt: skip
    net.status(LEVER_URL, 503)  # the Lever board fails this time
    first_ashby = {"jobs": [
        _ashby("ks-1", "Research Engineer, Foundation Models", "Zürich", secondary=["Paris"]),
        _ashby("ks-2", "AI Engineer (LLM agents)", "Remote - Europe", remote=True),
        _ashby("ks-3", "Product Designer", "Berlin"),
    ]}  # fmt: skip
    net.json(ASHBY_URL, first_ashby)


@contextmanager
def offline(net: OfflineNet):
    saved = collect.TRANSPORT_FACTORY, collect.RESOLVER, collect.TIMER
    collect.TRANSPORT_FACTORY, collect.RESOLVER, collect.TIMER = (lambda: net), net.resolve, net
    try:
        yield
    finally:
        collect.TRANSPORT_FACTORY, collect.RESOLVER, collect.TIMER = saved


def run(directory: Path | None = None) -> int:
    root = Path(directory) if directory else Path(tempfile.mkdtemp(prefix="job-radar-demo-"))
    if (root / workspace.DB_NAME).exists():
        print(f"{root} already holds a workspace; pass a new --dir")
        return 2
    clock = FixedClock(START)
    workspace.init_workspace(root, kind="demo", clock=clock)
    session = workspace.open_session(root, clock=clock)
    results: list[tuple[bool, str]] = []

    def check(ok: bool, text: str) -> None:
        results.append((bool(ok), text))
        print(f"  {'PASS' if ok else 'FAIL'}  {text}")

    print(f"Demo workspace (synthetic data only): {root}\n")
    for company, url in (
        ("Northwind AI (synthetic)", "https://boards.greenhouse.io/example-northwind"),
        ("Fjord Pay (synthetic)", "https://jobs.eu.lever.co/example-fjord"),
        ("Kestrel Labs (synthetic)", "https://jobs.ashbyhq.com/example-kestrel"),
    ):
        applications.add_board(session, url=url, company=company, synthetic=True)
    ids = ["northwind-ai-synthetic", "fjord-pay-synthetic", "kestrel-labs-synthetic"]

    print("1. First collection")
    net = OfflineNet()
    first_round(net)
    with offline(net):
        summary = collect.run(session, ids)
    outcomes = {r.board.id: r.outcome for r in summary.results}
    check(
        all(o == "complete" for o in outcomes.values()),
        f"all three boards read completely {outcomes}",
    )
    check(sum(r.counts["new"] for r in summary.results) == 11, "11 postings stored")
    check(any(u.endswith("/robots.txt") for u in net.sent), "robots.txt read before each API host")

    print("\n2. Screening")
    relevant = {p["title"]: p for p in views.inbox(session, view="relevant")}
    outside = {p["title"]: p for p in views.inbox(session, view="outside")}
    check("Machine Learning Engineer, Ranking" in relevant, "ML role in Amsterdam matches")
    check("Graduate Software Engineer" in relevant, "graduate role in Stockholm matches")
    check("AI Engineer (LLM agents)" in relevant, "remote role open to Europe matches")
    check("Data Engineer" in relevant and relevant["Data Engineer"]["verdict"].status == "check",
          "remote role with no region is flagged for a check, not assumed")  # fmt: skip
    check("Account Executive, DACH" in outside, "sales role ruled out")
    check("Software Engineer, Payments" in outside, "New York role ruled out by location")
    check(
        "Staff Infrastructure Engineer" in outside, "staff level ruled out by default preferences"
    )
    check("Product Designer" in outside, "design role ruled out")

    print("\n3. Your decisions")
    ml = relevant["Machine Learning Engineer, Ranking"]
    app_id = applications.promote(session, ml["short_id"], note="Strong ranking team")
    applications.set_status(session, app_id, "applied")
    applications.add_task(session, app_id, "Prepare for the ML system design round", kind="interview_prep",
                          due="2026-10-15")  # fmt: skip
    applications.set_next(session, app_id, "Follow up with the recruiter", due="2026-10-12")
    applications.decide(session, relevant["Quantitative Developer"]["short_id"], "dismissed",
                        note="not interested in trading")  # fmt: skip
    queue = views.queue(session)
    check(len(queue) == 1 and queue[0]["status"] == "applied", f"{app_id} recorded as applied")
    check(all(p["title"] != "Quantitative Developer" for p in views.inbox(session, view="relevant")),
          "dismissed posting left the inbox")  # fmt: skip

    print("\n4. Second collection a day later")
    clock.advance(days=1)
    net2 = OfflineNet()
    second_round(net2)
    with offline(net2):
        summary = collect.run(session, ids)
    outcomes = {r.board.id: r.outcome for r in summary.results}
    check(
        outcomes["fjord-pay-synthetic"] == "fetch_failed", "failed Lever board reported as failed"
    )
    fjord = [p for p in views.inbox(session, view="all") if p["board_id"] == "fjord-pay-synthetic"]
    check(all(p["state"] == "listed" for p in fjord), "a failed read marks nothing as withdrawn")
    detail = views.posting_detail(session, ml["short_id"])
    check(detail["state"] == "missing", "withdrawn posting marked 'no longer listed', not deleted")
    queue = views.queue(session)
    check(queue and queue[0]["status"] == "applied" and queue[0]["posting_state"] == "missing",
          "your application kept its status and shows the posting is gone")  # fmt: skip
    check(
        len(queue[0]["open_tasks"]) == 1 and queue[0]["next_action"], "tasks and next step survived"
    )
    new = [p["title"] for p in views.inbox(session, view="new")]
    check("Junior ML Engineer, Perception" in new, "new posting appears in the 'new' view")
    renamed = [p for p in views.inbox(session, view="all") if p["source_record_id"] == "9002"][0]
    fields = [h["field"] for h in views.posting_detail(session, renamed["short_id"])["history"]]
    check("title" in fields, "renamed posting's title change is in its history")
    check(all(p["title"] != "Quantitative Developer" for p in views.inbox(session, view="relevant")),
          "dismissal survived the re-collection")  # fmt: skip

    session.close()
    passed = sum(ok for ok, _ in results)
    print(f"\n{passed}/{len(results)} checks passed.")
    print("Explore it:")
    for command in ("inbox --details", "inbox --view outside", "queue", f"show {ml['short_id']}",
                    "boards", "runs 2 --requests"):  # fmt: skip
        print(f"  job-radar --workspace {root} {command}")
    return 0 if passed == len(results) else 1
