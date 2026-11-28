"""factory merger (brief 3.11, D24): runs from the default branch's `workflow_run` workflow, never from the PR.
It doesn't trust the PR's own factory/tier: it recomputes the tier with this pinned core and the base branch's config
(gates.assess, allow_head=False), adds changed-line coverage from the repo's CI artifact (G1), and squash-merges with
the merger App's token only if every condition holds. It never checks out or runs PR code."""
import io, json, os, sys, zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gates, tier  # noqa: E402
from gates import q  # noqa: E402

GREEN, MAX_REPORT = {"success", "neutral", "skipped"}, 50 << 20
FACTORY = ("factory/integrity", "factory/tier")

def coverage(api, cfg, pr):
    """An LCOV report from a successful `pull_request` run at the PR head, or why there is none. The report comes
    from PR code: it only counts at T0/T1, where the PR touches no gate or threshold file (D24)."""
    c, head = cfg["coverage"], pr["head"]["sha"]
    for run in api.get_all(f"actions/runs?head_sha={head}&event=pull_request&status=success", key="workflow_runs", pages=2):
        for a in api.get_all(f"actions/runs/{run['id']}/artifacts", key="artifacts", pages=1):
            if a.get("name") != c["artifact"] or a.get("expired"):
                continue
            if int(a.get("size_in_bytes") or 0) > MAX_REPORT:
                return f"artifact `{c['artifact']}` is over {MAX_REPORT >> 20} MB"
            try:
                z = zipfile.ZipFile(io.BytesIO(api.download(f"actions/artifacts/{a['id']}/zip")))
                info = z.getinfo(c["path"])
                if info.file_size > MAX_REPORT:
                    return f"{c['path']} in `{c['artifact']}` is over {MAX_REPORT >> 20} MB"
                return tier.parse_lcov(z.read(info).decode("utf-8", "replace"))
            except (TypeError, KeyError, zipfile.BadZipFile, OSError, SystemExit):  # Api ends a call with SystemExit
                continue
    return f"no `{c['artifact']}` artifact holding {c['path']} from a successful pull_request run at {head[:12]}"

def mutation_ceiling(cfg):
    """D26: the recorded mutation score caps the ceiling. >= 60% T1, 40-59% T0, below 40% no auto-merge (-1).
    No score recorded (no /factory:onboard sample yet): T0, which needs no tests."""
    m = cfg.get("mutation")
    score = m.get("score") if isinstance(m, dict) else None
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return 0
    return 1 if score >= 60 else 0 if score >= 40 else -1

def checks_red(api, head):
    """Every check run and commit status on the head must be green, and both factory statuses present."""
    bad = [c.get("name") for c in api.get_all(f"commits/{head}/check-runs", key="check_runs", pages=3)
           if c.get("status") != "completed" or c.get("conclusion") not in GREEN]
    st = {s["context"]: s["state"] for s in (api.get(f"commits/{head}/status") or {}).get("statuses", [])}
    return bad + [c for c, s in st.items() if s != "success"] + [f"{c} (missing)" for c in FACTORY if c not in st]

def decide(api, n, owner):
    """-> (merge?, reasons, head). Always the PR's current head, whichever run woke the merger."""
    pr, cfg, t, reasons, flags, _ = gates.assess(api, n, coverage=lambda c, p: coverage(api, c, p), allow_head=False)
    au, login, why, head = cfg["authority"], (pr.get("user") or {}).get("login"), [], pr["head"]["sha"]
    agents, ceiling = au["agents"] or [owner, "claude[bot]", "factory-agent[bot]"], au["auto_merge_ceiling"]
    if capped := (cap := mutation_ceiling(cfg)) < ceiling:
        ceiling = cap
    ceil = ("no auto-merge (mutation score below 40%)" if ceiling < 0 else
            f"auto_merge_ceiling T{ceiling}" + (" (capped by the mutation score)" if capped else ""))
    if pr.get("state") != "open" or pr.get("draft"):
        why.append("not an open, ready PR")
    if login not in agents:
        why.append(f"author {login} is not an agent identity ({', '.join(agents)})")
    if "hold" in {lb["name"] for lb in pr.get("labels", [])}:
        why.append("the `hold` label is on")
    if t > ceiling:
        why.append(f"T{t} > {ceil}: {reasons[0]}")
    if fails := [f.code for f in flags if f.kind == "fail"]:
        why.append(f"integrity fails: {', '.join(fails)}")
    if (api.get(f"compare/{q(pr['base']['ref'])}...{head}") or {}).get("behind_by", 1):
        why.append(f"behind {pr['base']['ref']}: update the branch")
    if red := checks_red(api, head):
        why.append(f"checks not green: {', '.join(map(str, red))}")
    return not why, why or [f"T{t} <= {ceil}: {reasons[0]}; {reasons[-1]}"], head

def main(env=os.environ, api=None, merge_api=None):
    run = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text("utf-8")).get("workflow_run") or {}
    repo, sha = env["GITHUB_REPOSITORY"], run.get("head_sha")
    api = api or gates.Api(repo, env["GH_TOKEN"])
    found = [p["number"] for p in run.get("pull_requests") or []]  # CI and the gates finish in either order
    found += [p["number"] for p in (api.get(f"commits/{sha}/pulls") or []) if p.get("state") == "open"] if sha else []
    failed = []
    for n in dict.fromkeys(found):
        try:
            ok, why, head = decide(api, n, repo.split("/")[0])
            print(f"factory merger: #{n} {'merging' if ok else 'not merging'}: " + "; ".join(why))
            if ok:  # `sha` makes GitHub refuse if the head moved since the checks above
                body, _ = (merge_api or gates.Api(repo, env["MERGE_TOKEN"])).call(
                    "PUT", f"pulls/{n}/merge", {"merge_method": "squash", "sha": head})
                if not json.loads(body or b"{}").get("merged"):
                    raise SystemExit(f"factory merger: #{n} merge refused or not found")
        except SystemExit as e:
            print(e, file=sys.stderr)
            failed.append(n)
    if failed:
        raise SystemExit(f"factory merger: failed for {', '.join(f'#{n}' for n in failed)}")

if __name__ == "__main__":
    main()
