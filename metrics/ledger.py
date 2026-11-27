#!/usr/bin/env python3
"""factory ledger (spec: Ledger): append | summary | audit. Data is machine-local, in ~/.factory/metrics/ (D18).
Transcripts are read from $CLAUDE_CONFIG_DIR/projects (default ~/.claude/projects); unknown lines are skipped."""
import argparse, csv, datetime, json, math, os, statistics, subprocess, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "factory" / "lib"))
import factory_lib as fl  # noqa: E402

FIELDS = ["date", "repo", "issue", "tier", "tier_src", "sessions", "tokens", "pr", "packet_ok", "parent"]
KINDS = {"input_tokens": "input", "output_tokens": "output", "cache_creation_input_tokens": "cache_creation",
         "cache_read_input_tokens": "cache_read"}
BUILD_PROMPT, SHAPE_PROMPTS = "Implement the spec in", ("/factory:shape", "<command-message>factory:shape</command-message>")

def jsonl(path):  # parsed dict lines; unreadable or malformed lines are skipped
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    yield d
    except OSError:
        return

def sessions():  # {session id: (main lines without sidechains, subagent file paths)}
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    return {m.stem: ([d for d in jsonl(m) if not d.get("isSidechain")], sorted((m.parent / m.stem / "subagents").glob("*.jsonl")))
            for m in root.glob("*/*.jsonl")}

def under(cwd, root):
    try:
        return Path(cwd).resolve().is_relative_to(Path(root).resolve())
    except (OSError, TypeError, ValueError):
        return False

def usage(branch, worktree):  # -> (sessions, {model: {input, output, cache_creation, cache_read}})
    tok, n = defaultdict(lambda: dict.fromkeys(KINDS.values(), 0)), 0
    for mains, subs in sessions().values():
        hits = [d for d in mains if d.get("gitBranch") == branch and under(d.get("cwd") or "", worktree)]
        if not hits:
            continue
        n, seen = n + 1, set()
        for d in hits + [x for s in subs for x in jsonl(s)]:  # subagent files count through their parent session
            m = d.get("message")
            if not isinstance(m, dict) or not isinstance(m.get("usage"), dict) or (m.get("id") and m["id"] in seen):
                continue
            seen.add(m.get("id"))  # one assistant message spans several lines, each repeating its usage
            for k, name in KINDS.items():
                tok[m.get("model") or "unknown"][name] += int(m["usage"].get(k) or 0)
    return n, dict(tok)

def pr_tier(url):  # the PR's tier:T<n> label after gates ran, or None
    try:
        r = subprocess.run(["gh", "pr", "view", url, "--json", "labels"], capture_output=True, text=True, encoding="utf-8", timeout=60)
        labels = json.loads(r.stdout)["labels"] if r.returncode == 0 else []
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        labels = []
    return max((int(lb["name"][6:]) for lb in labels if lb.get("name", "")[6:].isdigit() and lb["name"].startswith("tier:T")), default=None)

def packet_ok(worktree, tier):
    ctx = fl.repo_ctx(worktree)
    p = ctx["work"] / "packet.md" if ctx and ctx["work"] and ctx["cfg"] else None
    return bool(ctx and p and p.is_file() and not fl.packet_check(p.read_text("utf-8"), ctx["cfg"], int(tier)))

def read_csv(path):  # rows hold no embedded newlines (tokens is one-line JSON)
    return list(csv.DictReader(Path(path).read_text("utf-8").splitlines())) if Path(path).is_file() else []

def cmd_append(a):
    n, tok = usage(a.branch, a.worktree)
    t = pr_tier(a.pr)
    row = {"date": datetime.date.today().isoformat(), "repo": a.repo, "issue": a.issue, "tier": a.tier if t is None else t,
           "tier_src": "declared" if t is None else "label", "sessions": n, "tokens": json.dumps(tok, sort_keys=True),
           "pr": a.pr, "packet_ok": str(packet_ok(a.worktree, a.tier)).lower(), "parent": a.parent or ""}
    p = fl.home() / "metrics" / "ledger.csv"
    rows = [r for r in read_csv(p) if r["pr"] != a.pr] + [row]  # re-shipping or fx clean replaces the row
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_suffix(".tmp"), "w", encoding="utf-8", newline="") as f:
        f.write(",".join(FIELDS) + "\r\n")  # the header csv.DictWriter.writeheader() would write
        csv.DictWriter(f, FIELDS).writerows(rows)
    os.replace(p.with_suffix(".tmp"), p)
    print(f"ledger: {a.repo}#{a.issue} T{row['tier']} ({row['tier_src']}), {n} sessions, packet_ok={row['packet_ok']}")

def total(tokens):  # input + output + cache writes; cache reads are reported separately
    try:
        return sum(v["input"] + v["output"] + v["cache_creation"] for v in json.loads(tokens).values())
    except (ValueError, TypeError, KeyError, AttributeError):
        return None

def cmd_summary(a):
    rows = read_csv(fl.home() / "metrics" / "ledger.csv")
    print("tokens per change (input + output + cache writes)\ntier  n  median  p90")
    for t in sorted({r["tier"] for r in rows}):
        xs = sorted(x for x in (total(r["tokens"]) for r in rows if r["tier"] == t) if x is not None)
        if xs:
            print(f"T{t}  {len(xs)}  {statistics.median(xs):.0f}  {xs[math.ceil(0.9 * len(xs)) - 1]}")
    paired = sum(len({r["repo"] for r in rows if r["parent"] == par}) >= 2 for par in {r["parent"] for r in rows} - {""})
    print(f"shipped={len(rows)} t2={sum(r['tier'] == '2' for r in rows)} t3={sum(r['tier'] == '3' for r in rows)} paired={paired} "
          f"packet_ok={sum(r['packet_ok'] == 'true' for r in rows)}/{len(rows)} declared_tier={sum(r['tier_src'] == 'declared' for r in rows)}")
    weeks = read_csv(fl.home() / "metrics" / "weekly.csv")
    print("weekly /usage %: " + (", ".join(f"{w['week']} {w['usage_pct'] or '-'}" for w in weeks) or "none recorded"))

def first_prompt(mains):
    for d in mains:
        if d.get("type") == "user" and not d.get("isMeta") and isinstance(d.get("message"), dict):
            c = d["message"].get("content")
            return c if isinstance(c, str) else " ".join(b.get("text", "") for b in c or [] if isinstance(b, dict))
    return ""

def build_wt(cwd):  # cwd is in a factory worktree whose state.json says build (the spec's unguarded rule)
    for d in [Path(cwd), *Path(cwd).parents] if cwd else []:
        if (d / ".factory" / "work").is_dir():
            return any(fl.load_json(s, {}).get("phase") == "build" for s in (d / ".factory" / "work").glob("*/state.json"))
    return False

def cmd_audit(a):
    logs = list(jsonl(fl.home() / "logs" / "hooks.jsonl"))
    stops = [x for x in logs if x.get("hook") == "stop_verify"]
    last = {x.get("session_id"): x.get("decision") for x in stops}  # each session's last Stop decision
    build = {x.get("session_id") for x in logs if x.get("hook") == "reinject" and x.get("phase") == "build"}
    build |= {sid for sid, (mains, _) in sessions().items() if (p := first_prompt(mains).lstrip()).startswith(BUILD_PROMPT)
              or not p.startswith(SHAPE_PROMPTS) and any(build_wt(c) for c in {d.get("cwd") for d in mains})}
    unguarded = sorted(str(s) for s in build - {x.get("session_id") for x in stops} if s)
    shipped_ok = {(x.get("repo"), str(x.get("issue"))) for x in logs
                  if x.get("hook") == "fx_ship" and x.get("decision") == "ship" and x.get("verify_ok") is True}
    out = {"red_done": sum(x.get("decision") == "allow" and x.get("reason") != "blocker" and x.get("verify_ok") is not True for x in stops),
           "unguarded_build_sessions": len(unguarded),
           "shipped_red": sum((r["repo"], r["issue"]) not in shipped_ok for r in read_csv(fl.home() / "metrics" / "ledger.csv")),
           "ended_by_cap_or_interrupt": sum(d == "block" for d in last.values()),
           "test_lock_denies": sum(x.get("hook") == "test_lock" and x.get("decision") == "deny" for x in logs),
           "over_budget": sum(x.get("hook") == "verify" and x.get("decision") == "over_budget" for x in logs)}
    print("\n".join(f"{k}={v}" for k, v in out.items()))
    if unguarded:
        print("unguarded sessions: " + ", ".join(unguarded))

def main(argv=None):
    p = argparse.ArgumentParser(prog="ledger.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    ap, _, _ = sub.add_parser("append"), sub.add_parser("summary"), sub.add_parser("audit")
    for k in ("repo", "issue", "tier", "branch", "worktree", "pr", "parent"):
        ap.add_argument(f"--{k}", required=k != "parent")
    a = p.parse_args(argv)
    return globals()[f"cmd_{a.cmd}"](a) or 0

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.exit(main())
