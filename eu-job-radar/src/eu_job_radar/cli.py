"""The `job-radar` command line."""

import argparse
import json
import sys
import textwrap
from pathlib import Path

from . import __version__, applications, boards, collect, views, windows, workspace
from .applications import STATUSES, UserError
from .db import SchemaError
from .screening import FAMILY_LABELS, SENIORITY_LABELS, PreferencesError

MARK = {"relevant": "✓", "check": "?", "outside": "✗"}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 0
    try:
        return args.handler(args) or 0
    except (
        UserError,
        PreferencesError,
        workspace.WorkspaceError,
        boards.BoardError,
        collect.CollectError,
        SchemaError,
        windows.WindowError,
    ) as exc:
        print(f"job-radar: {exc}", file=sys.stderr)
        return 2


def _session(args) -> workspace.Session:
    return workspace.open_session(Path(args.workspace) if args.workspace else None)


def _wrap(text: str, indent: str = "    ") -> str:
    return textwrap.fill(text, width=100, initial_indent=indent, subsequent_indent=indent)


# --- setup ------------------------------------------------------------------------------


def cmd_init(args) -> int:
    root = Path(args.workspace).expanduser() if args.workspace else workspace.default_root()
    kind = "validation" if args.validation else "real"
    workspace.init_workspace(root, kind=kind)
    print(f"Created a {kind} workspace at {root}")
    print(f"  preferences: {root / workspace.PREFERENCES}  (edit roles, levels and countries)")
    print(f"  settings:    {root / workspace.SETTINGS}")
    print("Next: `job-radar boards` to see the company boards, then `job-radar collect --all`.")
    return 0


def cmd_validate(args) -> int:
    session = _session(args)
    prefs = session.preferences()
    print(f"Workspace {session.root} ({session.kind}); preferences are valid.")
    print("  roles:     " + ", ".join(FAMILY_LABELS[f] for f in sorted(prefs.families)))
    print("  levels:    " + ", ".join(SENIORITY_LABELS[s] for s in sorted(prefs.seniority)))
    print(f"  countries: {len(prefs.countries)} ({' '.join(sorted(prefs.countries))})")
    print(f"  remote open to Europe/EMEA/worldwide: {'kept' if prefs.remote_europe else 'dropped'}")
    problems = []
    for board in boards.all_boards(session.conn):
        problems += boards.validate_board(board)
    for problem in problems:
        print(f"  board problem: {problem}")
    return 1 if problems else 0


# --- boards -----------------------------------------------------------------------------


def cmd_boards(args) -> int:
    session = _session(args)
    conn = session.conn
    health = collect.health(conn)
    settings = boards.enabled_map(conn)
    if args.board:
        board = boards.get(conn, args.board)
        info = health.get(board.id, {})
        enabled, note = settings.get(board.id, (True, None))
        print(f"{board.company}  [{board.id}]")
        print(
            f"  ATS:        {board.ats}, token '{board.token}'"
            + (f", region {board.region}" if board.region else "")
        )
        print(f"  from:       {board.origin}; token {board.token_status}")
        print(f"  enabled:    {'yes' if enabled else 'no'}" + (f" ({note})" if note else ""))
        if board.careers:
            print(f"  careers:    {board.careers}")
        print(f"  checks:     {info.get('checks', 0)}; last {info.get('last_outcome', 'never')}"
              f" at {info.get('last_at', '-')}")  # fmt: skip
        if info.get("last_detail"):
            print(f"  detail:     {info['last_detail']}")
        print(f"  postings:   {info.get('listed', 0)} listed, {info.get('missing', 0)} missing")
        return 0
    rows = boards.all_boards(conn)
    if session.kind == "demo":
        rows = [b for b in rows if b.synthetic]
    print(f"{'BOARD':<24} {'COMPANY':<22} {'ATS':<10} {'LAST CHECK':<12} {'POSTINGS':>8}  STATUS")
    for board in rows:
        info = health.get(board.id, {})
        enabled, _ = settings.get(board.id, (True, None))
        status = info.get("last_outcome", "not checked yet")
        if info.get("failures_in_a_row", 0) > 1:
            status += f" ({info['failures_in_a_row']} failures in a row)"
        if not enabled:
            status = "disabled"
        print(
            f"{board.id:<24} {board.company[:22]:<22} {board.ats:<10} "
            f"{(info.get('last_at') or '-')[:10]:<12} {info.get('listed', 0):>8}  {status}"
        )
    print(f"\n{len(rows)} boards. Tokens marked unverified are checked by your first collection.")
    return 0


def cmd_boards_add(args) -> int:
    session = _session(args)
    board_id = applications.add_board(
        session, url=args.url, ats=args.ats, token=args.token, company=args.company,
        region=args.region, kind=args.kind, board_id=args.id,
    )  # fmt: skip
    print(f"Added board {board_id}. Collect it with `job-radar collect {board_id}`.")
    return 0


def cmd_boards_toggle(args) -> int:
    session = _session(args)
    applications.set_board_enabled(session, args.board, args.enable, args.note)
    print(f"{args.board} {'enabled' if args.enable else 'disabled'}.")
    return 0


# --- collection -------------------------------------------------------------------------


def cmd_collect(args) -> int:
    session = _session(args)
    summary = collect.run(session, args.boards, all_enabled=args.all, budget=args.budget)
    for board_id, reason in summary.refused:
        print(f"  refused {board_id}: {reason}")
    totals = {"new": 0, "changed": 0, "missing": 0}
    for r in summary.results:
        c = r.counts
        for key in totals:
            totals[key] += c.get(key, 0)
        line = f"  {r.board.id:<24} {r.outcome:<13} {c.get('seen', 0):>4} seen"
        line += (
            f", {c.get('new', 0)} new, {c.get('changed', 0)} changed, {c.get('missing', 0)} missing"
        )
        print(line)
        if r.detail:
            print(f"      {r.detail}")
    print(
        f"Run {summary.run_id}: {len(summary.results)} board(s), {summary.requests} of "
        f"{summary.budget} requests. {totals['new']} new, {totals['changed']} changed, "
        f"{totals['missing']} no longer listed."
    )
    print("Next: `job-radar inbox --view new`.")
    return 0


def cmd_runs(args) -> int:
    session = _session(args)
    conn = session.conn
    if args.run is None:
        for run in conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 20"):
            n = len(json.loads(run["boards"]))
            print(f"run {run['id']:<4} {run['started_at']}  {run['status']:<11} {n} board(s), "
                  f"{run['requests']} request(s)")  # fmt: skip
        return 0
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (args.run,)).fetchone()
    if run is None:
        raise UserError(f"no run {args.run}")
    print(f"run {run['id']}: {run['status']}, {run['requests']} of {run['budget']} requests")
    for check in conn.execute("SELECT * FROM checks WHERE run_id = ? ORDER BY id", (args.run,)):
        print(f"  {check['board_id']:<24} {check['outcome']:<13} seen {check['seen']}, "
              f"new {check['new']}, changed {check['changed']}, missing {check['missing']}, "
              f"unusable {check['quarantined']}")  # fmt: skip
        if check["detail"]:
            print(f"      {check['detail']}")
    if args.requests:
        for r in conn.execute("SELECT * FROM fetch_log WHERE run_id = ? ORDER BY id", (args.run,)):
            print(f"    {r['at']} {r['purpose']:<8} {r['status'] or '-':<4} {r['result']:<12} "
                  f"{r['url']}" + (f"  ({r['detail']})" if r["detail"] else ""))  # fmt: skip
    return 0


# --- inbox and decisions ------------------------------------------------------------------


def cmd_inbox(args) -> int:
    session = _session(args)
    items = views.inbox(session, view=args.view, country=args.country, family=args.family,
                        company=args.company, search=args.search, limit=args.limit)  # fmt: skip
    print(f"{args.view}: {views.INBOX_VIEWS[args.view]} — {len(items)} posting(s)\n")
    for item in items:
        v = item["verdict"]
        where = "; ".join(item["locations"][:3]) or "location not given"
        if len(item["locations"]) > 3:
            where += f" (+{len(item['locations']) - 3})"
        flag = " [no longer listed]" if item["state"] == "missing" else ""
        print(f"{MARK[v.status]} {item['short_id']}  {item['company']} — {item['title']}{flag}")
        fams = ", ".join(FAMILY_LABELS.get(f, f) for f in v.families) or "no role family"
        print(f"    {where} · {fams} · {SENIORITY_LABELS[v.seniority]}")
        if v.reasons and (args.details or v.status != "relevant"):
            print(_wrap("why: " + "; ".join(v.reasons)))
        if args.details:
            if v.notes:
                print(_wrap("passed: " + "; ".join(v.notes)))
            print(f"    first seen {item['first_seen_at'][:10]} · {item['url'] or 'no link'}")
    if items and args.view in {"relevant", "new"}:
        print("\n✓ matches  ? needs a check.  Decide with `job-radar promote|dismiss|later <id>`.")
    return 0


def cmd_show(args) -> int:
    session = _session(args)
    item = views.posting_detail(session, args.posting)
    v = item["verdict"]
    print(f"{item['company']} — {item['title']}  [{item['short_id']}]")
    print(f"  screen:     {v.status}" + (f" ({'; '.join(v.reasons)})" if v.reasons else ""))
    print(
        f"  role:       {', '.join(FAMILY_LABELS.get(f, f) for f in v.families) or 'none recognised'}"
    )
    print(f"  level:      {SENIORITY_LABELS[v.seniority]}")
    print(f"  locations:  {'; '.join(item['locations']) or 'not given'}"
          + (" (remote)" if v.remote else ""))  # fmt: skip
    print(f"  countries:  {' '.join(v.countries) or 'none read'}")
    for key in ("department", "employment_type", "published_at"):
        if item.get(key):
            print(f"  {key.replace('_', ' ') + ':':<11} {item[key]}")
    print(f"  link:       {item['url'] or 'none'}")
    print(f"  board:      {item['board_id']} (record {item['source_record_id']})")
    state = item["state"] + (f" since {item['missing_since']}" if item["missing_since"] else "")
    print(
        f"  state:      {state}; first seen {item['first_seen_at']}, last seen {item['last_seen_at']}"
    )
    if item["triage"]:
        t = item["triage"]
        print(f"  decision:   {t['decision']} on {t['at'][:10]}"
              + (f" → {t['application_id']}" if t["application_id"] else "")
              + (f" ({t['note']})" if t["note"] else ""))  # fmt: skip
    print(f"  history ({item['snapshots']} snapshot(s)):")
    for h in item["history"]:
        change = f"{h['old']} → {h['new']}" if h["old"] is not None else str(h["new"])
        print(f"    {h['at']} {h['actor_type']:<9} {h['field']}: {change}"
              + (f" ({h['note']})" if h["note"] else ""))  # fmt: skip
    return 0


def cmd_promote(args) -> int:
    session = _session(args)
    app_id = applications.promote(session, args.posting, note=args.note)
    print(
        f'Added to your queue as {app_id}. Next: `job-radar app next {app_id} "..." --due YYYY-MM-DD`.'
    )
    return 0


def cmd_decide(args) -> int:
    session = _session(args)
    if args.decision == "undo":
        applications.undo_decision(session, args.posting)
        print(f"Decision on {args.posting} undone; it is back in the inbox.")
    else:
        applications.decide(session, args.posting, args.decision, note=args.note)
        print(f"{args.posting} {args.decision}.")
    return 0


# --- applications -----------------------------------------------------------------------


def cmd_app_add(args) -> int:
    session = _session(args)
    app_id = applications.add_manual(session, company=args.company, title=args.title, url=args.url,
                                     status=args.status, note=args.note)  # fmt: skip
    print(f"Added {app_id}.")
    return 0


def cmd_app_status(args) -> int:
    session = _session(args)
    applications.set_status(session, args.app, args.status, note=args.note, on=args.on)
    print(f"{args.app} is now {STATUSES[args.status]}.")
    return 0


def cmd_app_next(args) -> int:
    session = _session(args)
    applications.set_next(session, args.app, args.text or None, due=args.due)
    print(f"Next step for {args.app} set." if args.text else f"Next step for {args.app} cleared.")
    return 0


def cmd_app_note(args) -> int:
    session = _session(args)
    applications.add_note(session, args.app, args.text)
    print("Note added.")
    return 0


def cmd_task_add(args) -> int:
    session = _session(args)
    task_id = applications.add_task(session, args.app, args.text, kind=args.kind, due=args.due)
    print(f"Task {task_id} added.")
    return 0


def cmd_task_done(args) -> int:
    session = _session(args)
    applications.finish_task(session, args.task)
    print(f"Task {args.task} done.")
    return 0


def cmd_queue(args) -> int:
    session = _session(args)
    rows = views.queue(session, show_all=args.all)
    if not rows:
        print(
            "Your queue is empty. Promote postings from `job-radar inbox`, or `job-radar app add`."
        )
        return 0
    for row in rows:
        due = row["earliest_due"]
        flag = " OVERDUE" if row["overdue"] else " due soon" if row["due_soon"] else ""
        print(f"{row['id']}  {STATUSES[row['status']]:<14} {row['company']} — {row['title']}")
        if row["deadline"]:
            basis = row["deadline_basis"] or "user_reported"
            label = "" if basis == "current_cycle_announcement" else f" [{basis.replace('_', ' ')}]"
            print(f"    deadline: {row['deadline']}{label}")
        if row["next_action"]:
            print(
                f"    next: {row['next_action']}"
                + (f" (due {row['next_due']})" if row["next_due"] else "")
            )
        for task in row["open_tasks"]:
            print(
                f"    task {task['id']}: {task['text']} [{task['kind']}]"
                + (f" due {task['due']}" if task["due"] else "")
            )
        if due and flag:
            print(f"   {flag.strip()}: {due}")
        if row.get("posting_state") == "missing":
            print(f"    note: the posting is no longer listed (since {row['missing_since'][:10]}); "
                  "check before you apply")  # fmt: skip
        if row["applied_at"]:
            print(f"    applied {row['applied_at']}")
    return 0


def cmd_export(args) -> int:
    session = _session(args)
    data = views.export_data(session)
    out = Path(args.out).expanduser() if args.out else session.root / "exports"
    out.mkdir(parents=True, exist_ok=True)
    stamp = session.today
    if args.format == "json":
        path = out / f"job-radar-{stamp}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {path}")
    else:
        apps = out / f"applications-{stamp}.csv"
        apps.write_text(views.to_csv(data["applications"], views.APP_FIELDS), encoding="utf-8")
        posts = out / f"relevant-postings-{stamp}.csv"
        posts.write_text(
            views.to_csv(data["relevant_postings"], views.POSTING_FIELDS), encoding="utf-8"
        )
        print(f"Wrote {apps}\nWrote {posts}")
    return 0


# --- hiring windows and deadlines -----------------------------------------------------------

BASIS_MARK = {
    "current_cycle_announcement": "announced",
    "owner_reported": "reported by owner, unchecked",
    "user_reported": "reported by you, unchecked",
    "not_yet_verified": "unknown",
}


def cmd_windows(args) -> int:
    session = _session(args)
    conn = session.conn
    if args.window:
        w = windows.get(conn, args.window)
        print(f"{w.company}: {w.programme}  [{w.id}]")
        print(f"  cycle:    {w.cycle or '-'} · {w.kind} · {w.where or '-'}")
        print(f"  page:     {w.page}")
        print(f"  opens:    {w.opens.describe()}")
        print(f"  closes:   {w.closes.describe()}")
        for claim in (w.opens, w.closes):
            if claim.wording:
                print(_wrap(f'quoted: "{claim.wording}"'))
            if claim.note:
                print(_wrap(f"note: {claim.note}"))
            if claim.source:
                print(f"    source: {claim.source}")
        if w.pattern:
            print(_wrap(f"usual pattern (not a date): {w.pattern}"))
        return 0
    rows = windows.all_windows(conn)
    if args.kind:
        rows = [w for w in rows if w.kind == args.kind]
    print(f"{'WINDOW':<36} {'COMPANY':<24} {'CLOSES':<12} BASIS")
    for w in rows:
        print(f"{w.id:<36} {w.company[:24]:<24} {w.closes.date or '?':<12} "
              f"{BASIS_MARK[w.closes.basis]}")  # fmt: skip
    print(f"\n{len(rows)} windows. `job-radar windows <id>` for the page and usual pattern;")
    print("`job-radar windows-report <id> --closes YYYY-MM-DD --source URL` when you find a date.")
    return 0


def cmd_windows_report(args) -> int:
    session = _session(args)
    windows.report(session, args.window, closes=args.closes, opens=args.opens, time=args.time,
                   timezone=args.timezone, source=args.source, note=args.note,
                   company=args.company, programme=args.programme, page=args.page,
                   kind=args.kind, cycle=args.cycle)  # fmt: skip
    print(f"Recorded for {args.window}, labelled as reported by you until checked on the page.")
    return 0


def cmd_windows_track(args) -> int:
    session = _session(args)
    app_id = applications.track_window(session, args.window, note=args.note)
    print(f"Added to your queue as {app_id}.")
    return 0


def cmd_windows_check(args) -> int:
    session = _session(args)
    results = windows.check_pages(session, args.windows or None, budget=args.budget)
    for r in results:
        print(f"  {r.window.id:<36} {r.status:<10} {r.detail}")
    print(
        "This compares quoted wording only; it never changes a date. Update the registry by hand."
    )
    return 0


def cmd_deadlines(args) -> int:
    session = _session(args)
    rows = windows.deadlines(session, days=args.days, include_unknown=args.unknown)
    if not rows:
        print(f"No dated deadlines in the next {args.days} days. `job-radar windows` shows what is"
              " still unknown.")  # fmt: skip
        return 0
    for d in rows:
        if d["date"] is None:
            print(f"  ?           {d['what']}  (closing date unknown; {d['detail']})")
            continue
        left = d["days_left"]
        when = "today" if left == 0 else f"{left} day(s) ago" if left < 0 else f"in {left} day(s)"
        print(f"  {d['date']}  {d['event']:<20} {d['what']}  — {when}")
        if d["basis"] != "current_cycle_announcement":
            print(
                f"              {BASIS_MARK.get(d['basis'], d['basis'])}; check {d['page'] or 'the page'}"
            )
    return 0


def cmd_app_deadline(args) -> int:
    session = _session(args)
    applications.set_deadline(session, args.app, args.date or None, note=args.note)
    print(f"Deadline for {args.app} {'set' if args.date else 'cleared'}.")
    return 0


def cmd_demo(args) -> int:
    from . import demo

    return demo.run(Path(args.dir) if args.dir else None)


# --- parser ------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-radar",
        description="Find, screen and track software, ML and AI engineering jobs in the EU and UK.",
    )
    parser.add_argument("--version", action="version", version=f"job-radar {__version__}")
    parser.add_argument(
        "--workspace", help="workspace directory (default: ~/.local/share/eu-job-radar/workspace)"
    )
    sub = parser.add_subparsers(metavar="COMMAND")

    def add(name, handler, help_text, **kwargs):
        p = sub.add_parser(name, help=help_text, description=help_text, **kwargs)
        p.set_defaults(handler=handler)
        return p

    p = add("init", cmd_init, "create a workspace with default preferences")
    p.add_argument(
        "--validation", action="store_true", help="a scratch workspace for trying things"
    )
    add("validate", cmd_validate, "check preferences and the board registry")
    add("demo", cmd_demo, "run an offline demo on synthetic data").add_argument(
        "--dir", help="a new directory for the demo workspace"
    )

    p = add("boards", cmd_boards, "list company boards and their health, or show one")
    p.add_argument("board", nargs="?")
    p = add("boards-add", cmd_boards_add, "add a company board (stored in your workspace)")
    p.add_argument("--company", required=True)
    p.add_argument("--url", help="a board URL, e.g. https://jobs.lever.co/acme")
    p.add_argument("--ats", choices=["greenhouse", "lever", "ashby"])
    p.add_argument("--token")
    p.add_argument("--region", choices=["eu"], help="Lever EU-hosted boards")
    p.add_argument("--kind", help="a label, e.g. 'AI lab'")
    p.add_argument("--id", help="board id (default: from the company name)")
    for name, enable in (("boards-enable", True), ("boards-disable", False)):
        p = add(name, cmd_boards_toggle, f"{'re-enable' if enable else 'stop collecting'} a board")
        p.add_argument("board")
        p.add_argument("--note")
        p.set_defaults(enable=enable)

    p = add(
        "collect", cmd_collect, "read boards now (one request each, plus robots.txt per API host)"
    )
    p.add_argument("boards", nargs="*", help="board ids")
    p.add_argument("--all", action="store_true", help="every enabled board")
    p.add_argument(
        "--budget",
        type=int,
        default=collect.DEFAULT_BUDGET,
        help="most requests this run may make (default %(default)s)",
    )
    p = add("runs", cmd_runs, "list collection runs, or show one")
    p.add_argument("run", nargs="?", type=int)
    p.add_argument("--requests", action="store_true", help="list every request")

    p = add("inbox", cmd_inbox, "postings to decide on")
    p.add_argument("--view", default="relevant", choices=list(views.INBOX_VIEWS))
    p.add_argument("--country", help="ISO code, e.g. DE, NL, GB (or UK)")
    p.add_argument("--family", choices=sorted(FAMILY_LABELS))
    p.add_argument("--company")
    p.add_argument("--search")
    p.add_argument("--limit", type=int)
    p.add_argument("--details", action="store_true")
    add("show", cmd_show, "one posting with its screen and history").add_argument("posting")
    p = add("promote", cmd_promote, "put a posting in your application queue")
    p.add_argument("posting")
    p.add_argument("--note")
    for decision, text in (("dismiss", "dismissed"), ("later", "later"), ("undo", "undo")):
        p = add(decision, cmd_decide, {"dismiss": "dismiss a posting", "later": "keep a posting for later",
                                        "undo": "undo a dismiss or later"}[decision])  # fmt: skip
        p.add_argument("posting")
        p.add_argument("--note")
        p.set_defaults(decision=text)

    add("queue", cmd_queue, "your applications, soonest due first").add_argument(
        "--all", action="store_true", help="include finished applications"
    )
    p = add("app-add", cmd_app_add, "add an application by hand (e.g. a referral)")
    p.add_argument("--company", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--url")
    p.add_argument("--status", default="to_apply", choices=list(STATUSES))
    p.add_argument("--note")
    p = add("app-status", cmd_app_status, "record an application's status (you apply yourself)")
    p.add_argument("app")
    p.add_argument("status", choices=list(STATUSES))
    p.add_argument("--note")
    p.add_argument("--on", help="date it happened, YYYY-MM-DD (default today)")
    p = add("app-next", cmd_app_next, 'set (or clear with "") an application\'s next step')
    p.add_argument("app")
    p.add_argument("text")
    p.add_argument("--due")
    p = add("app-note", cmd_app_note, "add a dated note to an application")
    p.add_argument("app")
    p.add_argument("text")
    p = add("task-add", cmd_task_add, "add a task to an application")
    p.add_argument("app")
    p.add_argument("text")
    p.add_argument("--kind", default="other", choices=sorted(applications.TASK_KINDS))
    p.add_argument("--due")
    add("task-done", cmd_task_done, "mark a task done").add_argument("task", type=int)
    p = add("windows", cmd_windows, "internship and graduate programmes at large employers")
    p.add_argument("window", nargs="?")
    p.add_argument("--kind", choices=sorted(windows.KINDS))
    p = add("windows-report", cmd_windows_report, "record a date you found for a window")
    p.add_argument("window", help="a window id, or a new id for a programme not listed")
    p.add_argument("--closes", help="YYYY-MM-DD")
    p.add_argument("--opens", help="YYYY-MM-DD")
    p.add_argument("--time", help="HH:MM, if the page states one")
    p.add_argument("--timezone", help="as the page states it, e.g. BST, CET, AoE")
    p.add_argument("--source", help="where you read it")
    p.add_argument("--note")
    p.add_argument("--company", help="new windows only")
    p.add_argument("--programme", help="new windows only")
    p.add_argument("--page", help="new windows only: the official page")
    p.add_argument("--kind", default="internship", choices=sorted(windows.KINDS))
    p.add_argument("--cycle", help="e.g. 2027")
    p = add("windows-track", cmd_windows_track, "put a window in your application queue")
    p.add_argument("window")
    p.add_argument("--note")
    p = add("windows-check", cmd_windows_check, "re-read windows' official pages (network)")
    p.add_argument("windows", nargs="*")
    p.add_argument("--budget", type=int, default=30)
    p = add("deadlines", cmd_deadlines, "dated deadlines and openings, soonest first")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--unknown", action="store_true", help="also list windows with no known date")
    p = add("app-deadline", cmd_app_deadline, 'set (or clear with "") an application deadline')
    p.add_argument("app")
    p.add_argument("date")
    p.add_argument("--note")
    p = add("export", cmd_export, "write applications and relevant postings to CSV or JSON")
    p.add_argument("--format", choices=["csv", "json"], default="csv")
    p.add_argument("--out", help="directory (default: the workspace's exports/)")
    return parser
