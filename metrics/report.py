#!/usr/bin/env python3
"""factory report (brief Phase 3 tasks 4-5, D26). Reads GitHub through `gh api` and the machine-local ledger (D18).
  report.py [--repo o/r] [--days 30] [--out FILE]   the monthly markdown report, per tier
  report.py --weekly [--week YYYY-Www] [--repo o/r]  the G11 sample: 1 in 5 auto-merged T0/T1 PRs of one ISO week
                                                     (default: the last complete week)
The owner marks a sampled PR with the `audit:ok` or `escaped` label; the monthly report counts both."""
import argparse, datetime as dt, hashlib, json, math, re, statistics, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "factory" / "lib"))
import factory_lib as fl  # noqa: E402
import ledger  # noqa: E402

CLOSES = re.compile(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\b")
OK, ESCAPED, STAMP_S = "audit:ok", "escaped", 60

class GhError(Exception):
    pass

def gh(path):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise GhError(f"report: gh api {path} failed: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout or "null")

def opt(api, path):
    """A lookup the report can do without (a deleted or moved issue, a missing status): None, with a warning."""
    try:
        return api(path)
    except GhError as e:
        print(f"{e} (counted as unknown)", file=sys.stderr)
        return None

def when(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))

def tier_of(labels):
    return max((int(n[6:]) for lb in labels or [] if (n := lb.get("name", "")).startswith("tier:T") and n[6:].isdigit()),
               default=None)

def closed_prs(api, since, until, pages=10):
    """PRs closed (merged or not) in [since, until), newest updated first."""
    out = []
    for page in range(1, pages + 1):
        got = api(f"pulls?state=closed&sort=updated&direction=desc&per_page=100&page={page}") or []
        out += [p for p in got if p.get("closed_at") and since <= when(p["closed_at"]) < until]
        if len(got) < 100 or when(got[-1]["updated_at"]) < since:
            return out
    print(f"report: stopped after {pages * 100} closed PRs; older ones in the window are not counted", file=sys.stderr)
    return out

def facts(api, p):
    """One closed PR -> what the report counts (definitions: D26)."""
    f = {"n": p["number"], "title": p["title"], "tier": tier_of(p.get("labels")), "merged": bool(p.get("merged_at")),
         "labels": {lb["name"] for lb in p.get("labels", [])}, "url": p.get("html_url", "")}
    issue = int(m[-1]) if (m := CLOSES.findall(p.get("body") or "")) else None
    iss = opt(api, f"issues/{issue}") if issue else None
    f["declared"] = tier_of(iss.get("labels")) if iss else None
    st = {s["context"]: s["state"] for s in (opt(api, f"commits/{p['head']['sha']}/status") or {}).get("statuses", [])}
    f["caught"] = st.get("factory/integrity") == "failure" or (None not in (f["tier"], f["declared"])
                                                               and f["tier"] > f["declared"])
    if f["merged"]:
        created, merged = when(p["created_at"]), when(p["merged_at"])
        f["auto"] = ((opt(api, f"pulls/{p['number']}") or {}).get("merged_by") or {}).get("type") == "Bot"
        commits = opt(api, f"pulls/{p['number']}/commits?per_page=100") or []
        # author dates of non-merge commits: an update-branch merge or a rebase is not a change
        dates = [when(c["commit"]["author"]["date"]) for c in commits if len(c.get("parents") or []) < 2]
        f["rework"] = any(d > created for d in dates)  # a change made after the PR opened
        f["review_s"] = (merged - max([created, *dates])).total_seconds()
        f["lead_h"] = (merged - when(iss["created_at"])).total_seconds() / 3600 if iss else None
    return f

def tokens_by_pr(repo):
    out = {}
    for r in ledger.read_csv(fl.home() / "metrics" / "ledger.csv"):
        if r.get("repo") == repo and (m := re.search(r"/pull/(\d+)$", r.get("pr") or "")):
            out[int(m[1])] = ledger.total(r.get("tokens"))
    return out

def med(xs, fmt="{:.0f}"):
    xs = [x for x in xs if x is not None]
    return fmt.format(statistics.median(xs)) if xs else "-"

def pct(a, b):
    return f"{100 * a / b:.0f}% ({a}/{b})" if b else "-"

def month(repo, fs, since, until):
    tok = tokens_by_pr(repo)
    reverted = {m[1] for f in fs if f["merged"] and (m := re.match(r'^Revert "(.+)"$', f["title"]))}
    out = [f"# factory report: {repo}, {since:%Y-%m-%d} to {until:%Y-%m-%d}", "",
           "| tier | merged | auto-merged | rework | escaped | change failure | tokens/change | lead time h | "
           "review time min | rubber-stamps | gate yield |", "|" + "---|" * 11]
    for t in sorted({f["tier"] for f in fs}, key=lambda x: -1 if x is None else x):
        a = [f for f in fs if f["tier"] == t]
        m = [f for f in a if f["merged"]]
        human = [f for f in m if not f["auto"]]
        bad = [f for f in m if ESCAPED in f["labels"] or f["title"] in reverted]
        stamps = [f for f in human if (t or 0) >= 2 and f["review_s"] < STAMP_S]
        out.append(f"| {'?' if t is None else f'T{t}'} | {len(m)} | {sum(f['auto'] for f in m)} | "
                   f"{pct(sum(f['rework'] for f in m), len(m))} | {sum(ESCAPED in f['labels'] for f in m)} | "
                   f"{pct(len(bad), len(m))} | {med(tok.get(f['n']) for f in m)} | {med((f['lead_h'] for f in m), '{:.1f}')} | "
                   f"{med((f['review_s'] / 60 for f in human), '{:.1f}')} | {len(stamps)} | "
                   f"{sum(f['caught'] for f in a)} of {len(a)} closed |")
    auto = [f for f in fs if f["merged"] and f["auto"]]
    ok, esc = sum(OK in f["labels"] for f in auto), sum(ESCAPED in f["labels"] for f in auto)
    weeks = [w for w in ledger.read_csv(fl.home() / "metrics" / "weekly.csv") if w.get("week", "") >= f"{since:%G-W%V}"]
    out += ["", f"- G11 sample audit of auto-merged PRs: {ok} `{OK}`, {esc} `{ESCAPED}`, "
                f"{len(auto) - ok - esc} not sampled or unmarked.",
            "- Weekly /usage %: " + (", ".join(f"{w['week']} {w.get('usage_pct') or '-'}" for w in weeks)
                                      or "none recorded (no weekly.csv)") + ".",
            "- Not measured: Sentry release attribution of escaped defects (D26).",
            "- Definitions (D26): rework = a commit after the PR opened; change failure = `escaped` or reverted; "
            "lead time = closing issue opened -> merged; review time = last commit -> merge, human merges only; "
            f"rubber-stamp = a T2/T3 human merge under {STAMP_S} s; gate yield = closed PRs whose gates failed "
            "integrity or raised the tier above the issue's."]
    return "\n".join(out) + "\n"

def weekly(repo, fs, week):
    """The G11 sample: 1 in 5 (rounded up) auto-merged T0/T1 PRs of one ISO week, ranked by a per-PR hash of repo,
    week and number, so the picks don't change between runs and a later PR can't reshuffle the earlier ones."""
    pool = [f for f in fs if f["merged"] and f["auto"] and (f["tier"] or 0) <= 1]
    rank = lambda f: hashlib.sha256(f"{repo}:{week}:{f['n']}".encode()).hexdigest()  # noqa: E731
    pick = sorted(sorted(pool, key=rank)[:math.ceil(len(pool) / 5)], key=lambda f: f["n"])
    out = [f"# factory weekly sample: {repo}, {week} ({len(pick)} of {len(pool)} auto-merged T0/T1)", ""]
    for f in pick:
        mark = OK if OK in f["labels"] else ESCAPED if ESCAPED in f["labels"] else "unmarked"
        tier = "T?" if f["tier"] is None else f"T{f['tier']}"
        out.append(f"- #{f['n']} {f['title']} ({tier}, {mark}): {f['url']}")
    out += ["", f"Mark each after a 2-minute read: `gh pr edit <n> --add-label {OK}` or `--add-label {ESCAPED}`."]
    return "\n".join(out) + "\n"

def main(argv=None, api=None, now=None):
    ap = argparse.ArgumentParser(prog="report.py")
    ap.add_argument("--repo")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out")
    ap.add_argument("--weekly", action="store_true")
    ap.add_argument("--week", help="YYYY-Www, with --weekly (default: the last complete ISO week)")
    a = ap.parse_args(argv)
    try:
        repo = a.repo or gh("repos/{owner}/{repo}")["full_name"]
        api = api or (lambda path: gh(f"repos/{repo}/{path}"))
        until = now or dt.datetime.now(dt.timezone.utc)
        since = until - dt.timedelta(days=a.days)
        if a.weekly:
            if not re.fullmatch(r"\d{4}-W\d{2}", wk := a.week or f"{(until - dt.timedelta(weeks=1)):%G-W%V}"):
                ap.error(f"--week {wk}: use YYYY-Www, e.g. 2026-W48")
            y, w = int(wk[:4]), int(wk[6:])
            since = dt.datetime.combine(dt.date.fromisocalendar(y, w, 1), dt.time(), dt.timezone.utc)
            until = since + dt.timedelta(weeks=1)
        fs = [facts(api, p) for p in closed_prs(api, since, until)]
    except GhError as e:
        raise SystemExit(str(e))
    text = weekly(repo, fs, f"{since:%G-W%V}") if a.weekly else month(repo, fs, since, until)
    if a.out:
        Path(a.out).write_text(text, "utf-8")
        print(f"report: wrote {a.out}")
    else:
        if callable(rc := getattr(sys.stdout, "reconfigure", None)):
            rc(encoding="utf-8")  # titles can hold characters the Windows console code page can't encode
        sys.stdout.write(text)

if __name__ == "__main__":
    main()
