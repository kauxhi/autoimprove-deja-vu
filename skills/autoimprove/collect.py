#!/usr/bin/env python3
"""autoimprove collector, formula v2. Read-only, stdlib only, Python 3.9+, nothing leaves the machine.

Usage: collect.py [--days 7] [--end YYYY-MM-DD] [--extra PATH] [--repeat-days 30] [--ledger PATH]
                  [--scoreboard-row]                     -> JSON (or one markdown row) on stdout
       collect.py --selftest

--ledger PATH: optional JSON mistake ledger (missing = empty). Never written here; the updated
  ledger is printed as "ledger_next" and the caller saves it.
--extra PATH: optional JSON of connector counts gathered through MCP tools:
  {"deploys": {"total": int, "failed": int},
   "migrations": {"total": int, "fix": int}}
--scoreboard-row: print one SCOREBOARD.md table row instead of JSON.
Emits counts and ids only, never transcript text.
"""
import argparse, collections, datetime as dt, difflib, glob, json, os, re, subprocess, sys

# ============================== CONFIG (edit freely) ==============================
# Regexes counted as rule violations when an assistant text block matches one.
# Empty list = violations sub-score is n/a and drops out of the weights.
# Example: [r"[\N{EM DASH}\N{EN DASH}]"] counts em and en dashes.
BANNED_OUTPUT = []
# Regex for project folder names whose error text is never fingerprinted (sensitive work).
# Example: r"(?i)client-x|medical". None = fingerprint everything.
PRIVATE_PROJECTS = None
# Regex for project folders skipped entirely.
EXCLUDE_PROJECTS = r"observer-sessions"
# Repeat-task topics (unscored). Add your own, no formula bump needed.
TOPICS = {
    "auth-signin": r"\b(sign[- ]?in|log[- ]?in|sign[- ]?up|auth(entication)?|sso|magic link|otp)\b",
    "email-sending": r"\b(smtp|resend|sendgrid|mailgun|transactional e?mail|send (an? )?e?mails?|mail (connection|setup|server))\b",
    "deploy": r"\b(deploy(ment)?s?|vercel|redeploy)\b",
    "db-migration": r"\b(migrations?|rls|supabase)\b",
    "payments": r"\b(payouts?|payments?|stripe|checkout)\b",
    "file-export": r"\b(pdf|xlsx|csv|export)\b",
}
# ==================================================================================

FORMULA_VERSION = "v2"
PROJECTS = os.path.expanduser("~/.claude/projects")
IDLE_GAP = 300  # seconds; gaps longer than this do not count as active time
CORRECTION = re.compile(
    r"^\W*(no\b|nope\b|wrong\b|i said\b|again\b|stop\b|not what\b|that'?s not\b|"
    r"undo\b|revert\b|why did you\b|i told you\b|still (broken|wrong|not)\b)", re.I)
INTERRUPT = "[Request interrupted by user"
DENIED = ("Permission for this action was denied", "The user doesn't want to proceed",
          "auto mode classifier")
BANNED = [re.compile(p) for p in BANNED_OUTPUT]
TOPIC_RX = {k: re.compile(v, re.I) for k, v in TOPICS.items()}
# weight, and penalty points per event per active hour (rate signals only)
WEIGHTS = {"corrections": 20, "reprompts": 10, "violations": 15, "rework": 15,
           "wasted": 10, "wins": 15, "deploys": 10, "migrations": 5}
PENALTY = {"corrections": 20, "reprompts": 40, "violations": 10, "wasted": 5}


def clamp(x):
    return max(0.0, min(100.0, x))


def score(counts, hours, commits, sessions, extra=None):
    """counts: dict signal -> int or None (None = n/a, dropped from the weights).
    extra: connector counts dict (see module doc) or None."""
    sub = {}
    for k, p in PENALTY.items():
        sub[k] = None if counts.get(k) is None or not hours else clamp(100 - p * counts[k] / hours)
    sub["rework"] = None if counts.get("rework") is None or not commits \
        else clamp(100 * (1 - counts["rework"] / commits))
    sub["wins"] = None if not sessions else clamp(100 * counts["wins"] / sessions)
    d = (extra or {}).get("deploys") or {}
    m = (extra or {}).get("migrations") or {}
    sub["deploys"] = clamp(100 * (1 - d.get("failed", 0) / d["total"])) if d.get("total") else None
    sub["migrations"] = clamp(100 * (1 - m.get("fix", 0) / m["total"])) if m.get("total") else None
    live = {k: v for k, v in sub.items() if v is not None}
    w = sum(WEIGHTS[k] for k in live)
    total = round(sum(WEIGHTS[k] * v for k, v in live.items()) / w, 1) if w else None
    return total, {k: (None if v is None else round(v, 1)) for k, v in sub.items()}


_FP_SUBS = [
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"\"[^\"\n]{21,}\"|'[^'\n]{21,}'"), "<str>"),
    (re.compile(r"(?<![\w<])(?:~|\.{1,2})?/[^\s\"'`:,;)(\]\[]+"), "<path>"),
    (re.compile(r"\b[\w.\-]+(?:/[\w.\-]+)+"), "<path>"),
    (re.compile(r"\b[0-9a-f]{8,}\b"), "<id>"),
    (re.compile(r"\d+"), "<n>"),
]


def fingerprint(text):
    """Stable key for a failed tool result: first non-empty line, normalised."""
    lines = [l for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return ""
    # "Exit code N" alone says nothing: use the next line; a traceback's cause is its last line
    if len(lines) > 1 and re.match(r"exit code \d+$", lines[0].strip(), re.I):
        lines = lines[1:]
    if lines[0].startswith("Traceback"):
        lines = lines[-1:]
    # the harness echoes the offending command after "followed by:"; that tail is not the mistake
    s = lines[0].split(" followed by:")[0][:160].lower()
    for rx, rep in _FP_SUBS:
        s = rx.sub(rep, s)
    return " ".join(s.split())[:80]


def merge_ledger(ledger, week, found):
    """Pure. found: {fingerprint: {"tool": str, "count": int}}. Returns a new ledger."""
    out = {k: {**v, "weeks": list(v.get("weeks", []))} for k, v in ledger.items()}
    for fp, f in found.items():
        e = out.setdefault(fp, {"tool": f["tool"], "first_seen": week, "weeks": [],
                                "total": 0, "lesson": None})
        if week not in e["weeks"]:
            e["weeks"].append(week)
            e["total"] = e.get("total", 0) + f["count"]
    return out


def text_of(content):
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text")


def is_human(d, txt):
    return (d.get("origin") or {}).get("kind") == "human" and txt and not txt.startswith("<")


def scan_session(path, start, end, rstart=None):
    """Returns (weekly, repeat). weekly: stats from lines in [start, end) or None (no human
    prompt there). repeat: {id, date, topics, friction, failed} from lines in [rstart, end)
    or None (no human prompt there)."""
    rstart = rstart or start
    s = collections.Counter()
    stamps, rstamps, prompts, cwds = [], [], [], set()
    topics, rfriction, rfailed, rprompts = set(), 0, 0, 0
    names, errs = {}, []  # tool_use id -> tool name; (fingerprint, tool) per failed result
    project = os.path.basename(os.path.dirname(path))
    skip_fp = bool(PRIVATE_PROJECTS and re.search(PRIVATE_PROJECTS, project))
    for line in open(path, errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t, ts = d.get("type"), d.get("timestamp")
        if t not in ("user", "assistant") or not ts or d.get("isSidechain"):
            continue
        if t == "assistant" and isinstance(d.get("message", {}).get("content"), list):
            for b in d["message"]["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    names[b.get("id")] = b.get("name")
        when = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        wk, rp = start <= when < end, rstart <= when < end
        if not (wk or rp):
            continue
        content = d.get("message", {}).get("content") or ""
        if rp:
            rstamps.append(when.timestamp())
            if t == "user":
                txt = text_of(content).strip()
                if INTERRUPT in txt[:80]:
                    rfriction += 1
                if isinstance(content, list):
                    rfailed += sum(1 for b in content if isinstance(b, dict)
                                   and b.get("type") == "tool_result" and b.get("is_error"))
                if is_human(d, txt):
                    rprompts += 1
                    if CORRECTION.match(txt):
                        rfriction += 1
                    topics.update(k for k, rx in TOPIC_RX.items() if rx.search(txt))
        if not wk:
            continue
        stamps.append(when.timestamp())
        if d.get("cwd"):
            cwds.add(d["cwd"])
        if t == "user":
            txt = text_of(content).strip()
            if INTERRUPT in txt[:80]:
                s["interrupts"] += 1
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                        c = b.get("content")
                        c = c if isinstance(c, str) else json.dumps(c)
                        s["denied" if any(x in c for x in DENIED) else "tool_errors"] += 1
                        fp = "" if skip_fp else fingerprint(
                            text_of(b["content"]) if isinstance(b.get("content"), list) else c)
                        if fp:
                            errs.append((fp, names.get(b.get("tool_use_id")) or "?"))
            if is_human(d, txt):
                s["prompts"] += 1
                if CORRECTION.match(txt):
                    s["corrections"] += 1
                norm = " ".join(txt.lower().split())[:400]
                if len(norm) > 20 and any(
                        difflib.SequenceMatcher(None, norm, p).ratio() >= 0.8 for p in prompts):
                    s["reprompts"] += 1
                prompts.append(norm)
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text" and \
                        any(rx.search(b.get("text", "")) for rx in BANNED):
                    s["violations"] += 1
    sid = os.path.basename(path)[:-6]
    rep = None
    if rprompts:
        rep = {"id": sid, "date": dt.datetime.fromtimestamp(min(rstamps)).strftime("%Y-%m-%d"),
               "topics": topics, "friction": rfriction, "failed": rfailed}
    if not s["prompts"]:
        return None, rep
    stamps.sort()
    active = sum(min(b - a, IDLE_GAP) for a, b in zip(stamps, stamps[1:]))
    s["corrections"] += s["interrupts"]
    s["wasted"] = s["tool_errors"] + s["denied"]
    s["one_pass"] = int(not s["corrections"] and not s["reprompts"])
    weekly = {"id": sid, "project": project,
              "date": dt.datetime.fromtimestamp(stamps[0]).strftime("%Y-%m-%d"),
              "active_h": round(active / 3600, 2), "cwds": sorted(cwds), **s, "errors": errs}
    return weekly, rep


def git(repo, *a):
    r = subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def scan_git(cwds, start, end):
    """Local git only. Rework = own commit titled revert, or titled fix touching a file from
    an own non-fix commit in the prior 48 hours."""
    repos = {}
    for c in cwds:
        if os.path.isdir(c):
            common = git(c, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
            if common:
                repos.setdefault(common, c)
    commits, rework = 0, []
    for repo in repos.values():
        me = git(repo, "config", "user.name").strip()
        if not me:
            continue
        since = (start - dt.timedelta(hours=48)).isoformat()
        out = git(repo, "log", "--all", "--no-merges", f"--author={me}", f"--since={since}",
                  f"--until={end.isoformat()}", "--name-only", "--format=\x01%H\x02%ct\x02%s")
        log = []
        for chunk in out.split("\x01")[1:]:
            head, _, files = chunk.partition("\n")
            sha, ct, subj = head.split("\x02", 2)
            log.append((int(ct), sha, subj, set(filter(None, files.split("\n")))))
        log.sort()
        for i, (ct, sha, subj, files) in enumerate(log):
            if ct < start.timestamp():
                continue
            commits += 1
            if re.match(r"revert\b", subj, re.I) or (
                    re.match(r"fix\b", subj, re.I) and any(
                        ct - p[0] <= 48 * 3600 and files & p[3] and not re.match(r"fix\b", p[2], re.I)
                        for p in log[:i])):
                rework.append({"sha": sha[:8], "repo": os.path.basename(os.path.dirname(repo))})
    return len(repos), commits, rework


def repeat_tasks(reps, min_sessions=3):
    """Topics asked in >= min_sessions distinct sessions. Counts, ids and dates only."""
    by = collections.defaultdict(list)
    for r in reps:
        for t in r["topics"]:
            by[t].append(r)
    out = []
    for t, rs in by.items():
        if len(rs) < min_sessions:
            continue
        rs.sort(key=lambda r: (-r["friction"], -r["failed"]))
        out.append({"topic": t, "sessions": len(rs),
                    "first": min(r["date"] for r in rs), "last": max(r["date"] for r in rs),
                    "friction": sum(r["friction"] for r in rs),
                    "failed_tools": sum(r["failed"] for r in rs),
                    "session_ids": [r["id"] for r in rs[:5]]})
    out.sort(key=lambda x: (-x["sessions"], -x["friction"]))
    return out


def mistakes_report(sessions, ledger, week):
    """Unscored in v2. Returns (mistakes, repeat_mistakes, ledger_next)."""
    found = {}
    for s in sessions:
        for fp, tool in s.get("errors", []):
            f = found.setdefault(fp, {"tool": tool, "count": 0, "ids": []})
            f["count"] += 1
            if s["id"] not in f["ids"]:
                f["ids"].append(s["id"])
    rows = []
    for fp, f in found.items():
        e = ledger.get(fp) or {}
        before = any(w != week for w in e.get("weeks", []))
        rows.append({"fingerprint": fp, "tool": f["tool"], "count": f["count"],
                     "sessions": len(f["ids"]), "session_ids": f["ids"][:3], "seen_before": before,
                     "weeks_seen": len(set(e.get("weeks", [])) | {week}),
                     "lesson": e.get("lesson")})
    rows.sort(key=lambda r: (not r["seen_before"], -r["sessions"], -r["count"]))
    total = sum(r["count"] for r in rows)
    repeat = sum(r["count"] for r in rows if r["seen_before"])
    rep = {"errors_total": total, "errors_repeat": repeat,
           "deja_vu_rate": round(repeat / total, 3) if total and ledger else None,
           "ghosts": sum(1 for r in rows if r["seen_before"] and r["lesson"]),
           "within_week_repeats": sum(1 for r in rows if r["sessions"] >= 3)}
    return rows[:25], rep, merge_ledger(ledger, week, found)


def collect(days, end, extra=None, repeat_days=30, ledger=None):
    start = end - dt.timedelta(days=days)
    rstart = end - dt.timedelta(days=repeat_days)
    lo = min(start, rstart).timestamp()
    sessions, reps = [], []
    for f in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        if (EXCLUDE_PROJECTS and re.search(EXCLUDE_PROJECTS, f)) or os.path.getmtime(f) < lo:
            continue
        s, r = scan_session(f, start, end, rstart)
        if s:
            sessions.append(s)
        if r:
            reps.append(r)
    hours = round(sum(s["active_h"] for s in sessions), 2)
    tot = collections.Counter()
    for s in sessions:
        tot.update({k: v for k, v in s.items() if isinstance(v, int)})
    nrepos, commits, rework = scan_git({c for s in sessions for c in s["cwds"]}, start, end)
    counts = {"corrections": tot["corrections"], "reprompts": tot["reprompts"],
              "violations": tot["violations"] if BANNED else None, "wasted": tot["wasted"],
              "rework": len(rework) if commits else None, "wins": tot["one_pass"]}
    total, sub = score(counts, hours, commits, len(sessions), extra)
    mistakes, repeat_mistakes, ledger_next = mistakes_report(
        sessions, ledger or {}, end.strftime("%Y-%m-%d"))

    def top(key):
        return [{"session": s["id"], "date": s["date"], "project": s["project"], "count": s.get(key, 0)}
                for s in sorted(sessions, key=lambda s: -s.get(key, 0))[:3] if s.get(key)]
    return {
        "formula": FORMULA_VERSION,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "sessions": len(sessions), "active_hours": hours, "prompts": tot["prompts"],
        "repos": nrepos, "commits": commits,
        "counts": counts, "connectors": extra,
        "breakdown": {k: tot[k] for k in ("interrupts", "tool_errors", "denied")},
        "sync_score": total, "sub_scores": sub, "weights": WEIGHTS,
        "evidence": {"corrections": top("corrections"), "reprompts": top("reprompts"),
                     "violations": top("violations"), "wasted": top("wasted"),
                     "rework": rework[:5]},
        "repeat_window_days": repeat_days,
        "repeat_tasks": repeat_tasks(reps),
        "mistakes": mistakes, "repeat_mistakes": repeat_mistakes, "ledger_next": ledger_next,
    }


def scoreboard_row(r):
    """One SCOREBOARD.md row: week | formula | sync | deja vu | ghosts | 8 sub-scores | hours."""
    def f(v):
        return "n/a" if v is None else f"{v:.1f}"
    rm = r["repeat_mistakes"]
    dv = "n/a (baseline)" if rm["deja_vu_rate"] is None else f"{rm['deja_vu_rate'] * 100:.1f}%"
    week = r["window"]["end"][:10]
    cells = [week, r["formula"], f(r["sync_score"]), dv, str(rm["ghosts"])]
    cells += [f(r["sub_scores"][k]) for k in WEIGHTS]
    cells.append(f"{r['active_hours']:.1f}")
    return "| " + " | ".join(cells) + " |"


def selftest():
    c0 = {"corrections": 0, "reprompts": 0, "violations": 0, "wasted": 0, "rework": 0, "wins": 4}
    t, sub = score(c0, 10, 5, 4)
    assert t == 100.0, t
    t, sub = score({"corrections": 10, "reprompts": 0, "violations": 0, "wasted": 0,
                    "rework": None, "wins": 2}, 10, 0, 4)
    assert sub["corrections"] == 80.0 and sub["rework"] is None and sub["wins"] == 50.0
    assert sub["deploys"] is None and sub["migrations"] is None
    assert t == round((20 * 80 + 10 * 100 + 15 * 100 + 10 * 100 + 15 * 50) / 70, 1), t
    # violations n/a (empty BANNED_OUTPUT) drops out of the weights
    t, sub = score({**c0, "violations": None, "corrections": 10}, 10, 5, 4)
    assert sub["violations"] is None
    assert t == round((20 * 80 + 10 * 100 + 15 * 100 + 10 * 100 + 15 * 100) / 70, 1), t
    assert CORRECTION.match("No, I said the other file") and not CORRECTION.match("now add tests")
    assert score({}, 0, 0, 0)[0] is None
    # connector sub-scores
    ex = {"deploys": {"total": 10, "failed": 2}, "migrations": {"total": 4, "fix": 1}}
    t, sub = score(c0, 10, 5, 4, ex)
    assert sub["deploys"] == 80.0 and sub["migrations"] == 75.0, sub
    assert t == round((20 * 100 + 10 * 100 + 15 * 100 + 15 * 100 + 10 * 100 + 15 * 100
                       + 10 * 80 + 5 * 75) / 100, 1), t
    # repeat topics
    assert TOPIC_RX["email-sending"].search("connect mail through smtp or resend")
    assert not TOPIC_RX["email-sending"].search("email me the report")
    assert TOPIC_RX["auth-signin"].search("build a sign in screen")
    reps = [{"id": f"s{i}", "date": f"2026-11-0{i}", "topics": {"deploy"}, "friction": i, "failed": 0}
            for i in range(1, 4)] + [{"id": "x", "date": "2026-11-09", "topics": {"payments"},
                                      "friction": 9, "failed": 0}]
    rt = repeat_tasks(reps)
    assert len(rt) == 1 and rt[0]["topic"] == "deploy" and rt[0]["sessions"] == 3
    assert rt[0]["session_ids"][0] == "s3" and rt[0]["first"] == "2026-11-01"
    # fingerprints
    a = fingerprint("Exit code 1\nTraceback ... /home/x/y.py line 42")
    assert a == fingerprint("Exit code 1\nTraceback ... /home/q/z/w.py line 7")
    assert fingerprint("<tool_use_error>Blocked: sleep 45 followed by: echo ok. To wait") == \
        fingerprint("<tool_use_error>Blocked: sleep 9 followed by: curl -s http://x") == "<tool_use_error>blocked: sleep <n>"
    assert fingerprint("") == "" and fingerprint(None) == ""
    assert fingerprint("Error reading /home/x/y.py line 42") == \
        fingerprint("Error reading /tmp/a/b.py line 9") == "error reading <path> line <n>"
    assert fingerprint("fetch https://a.com/x?id=1 failed 0a1b2c3d4e") == "fetch <url> failed <id>"
    # ledger merge
    L = merge_ledger({}, "2026-11-18", {"fp": {"tool": "Bash", "count": 3}})
    assert L["fp"]["weeks"] == ["2026-11-18"] and L["fp"]["lesson"] is None and L["fp"]["total"] == 3
    old = {"fp": {"tool": "Bash", "first_seen": "2026-11-11", "weeks": ["2026-11-11"],
                  "total": 2, "lesson": "note-x"}}
    L = merge_ledger(old, "2026-11-18", {"fp": {"tool": "Bash", "count": 4}})
    assert L["fp"]["lesson"] == "note-x" and L["fp"]["weeks"] == ["2026-11-11", "2026-11-18"]
    assert L["fp"]["total"] == 6 and old["fp"]["total"] == 2
    L2 = merge_ledger(L, "2026-11-18", {"fp": {"tool": "Bash", "count": 4}})
    assert L2["fp"]["total"] == 6 and L2["fp"]["weeks"] == L["fp"]["weeks"]
    # deja vu rate and ghosts
    ss = [{"id": f"s{i}", "errors": [("fp", "Bash")]} for i in range(3)]
    m, r, _ = mistakes_report(ss, old, "2026-11-18")
    assert m[0]["seen_before"] and m[0]["weeks_seen"] == 2 and m[0]["lesson"] == "note-x"
    assert r["errors_repeat"] == 3 and r["deja_vu_rate"] == 1.0
    assert r["ghosts"] == 1 and r["within_week_repeats"] == 1
    assert mistakes_report(ss, {}, "2026-11-18")[1]["deja_vu_rate"] is None
    # scoreboard row
    row = scoreboard_row({"window": {"end": "2026-11-18T00:00:00"}, "formula": "v2",
                          "sync_score": 80.9, "active_hours": 133.9,
                          "repeat_mistakes": {"deja_vu_rate": None, "ghosts": 0},
                          "sub_scores": {k: 100.0 for k in WEIGHTS}})
    assert row.startswith("| 2026-11-18 | v2 | 80.9 | n/a (baseline) | 0 | 100.0") and \
        row.endswith("| 133.9 |") and row.count("|") == 15, row
    print("selftest ok")


def load_json(path):
    try:
        with open(os.path.expanduser(path)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--end", help="YYYY-MM-DD local, exclusive; default now")
    ap.add_argument("--extra", help="JSON file of connector counts (deploys, migrations)")
    ap.add_argument("--repeat-days", type=int, default=30)
    ap.add_argument("--ledger", help="JSON mistake ledger (read only; ledger_next is printed)")
    ap.add_argument("--scoreboard-row", action="store_true", help="print one SCOREBOARD.md row")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        sys.exit()
    end = (dt.datetime.strptime(a.end, "%Y-%m-%d").astimezone() if a.end
           else dt.datetime.now().astimezone())
    extra = load_json(a.extra) if a.extra else None
    ledger = (load_json(a.ledger) if a.ledger else None) or {}
    result = collect(a.days, end, extra, a.repeat_days, ledger)
    if a.scoreboard_row:
        print(scoreboard_row(result))
    else:
        json.dump(result, sys.stdout, indent=1)
