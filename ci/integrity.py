"""factory/integrity: pure check(pr, spec, cfg, events) -> [Flag] (brief §3.10.2; spec: CI > ci/integrity.py).

pr     GitHub PR object plus "files" (list PR files, with patches) and "files_truncated" (bool).
spec   None, or {"meta", "sections", "path" (spec file at head; None for an issue-body spec), "issue"}.
events {"tier": final tier or None (first pass), "declared": declared tier (issue label or spec; only I7 reads it),
        "compare": compare(spec_commit...head) JSON or None, "label_events": issue events,
        "pins": [{"run": {"event", "path"}, "pin": pin.json}], "spec_blob": sha,
        "acc_lock": None or {"glob": the base config's acceptance_glob, "first": the PR commit that first touched it,
                    "compare": compare(first...head) JSON or None}}.
Comments are never an input: a comment can be posted by any workflow as github-actions[bot]."""
import collections, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "factory" / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import factory_lib as fl  # noqa: E402
from tier import names, tier_num  # noqa: E402

Flag = collections.namedtuple("Flag", "code kind msg")  # kind: "fail" | "raise:T2" | "raise:T3"
# Stacks as data (D23): an overlay's [integrity] assert/skip/suppress lists are added to these, never replace them.
ASSERT = [r"\bassert\b", r"\bself\.assert\w*", r"\bpytest\.raises\b", r"\bexpect\("]
SKIP = [r"@pytest\.mark\.skip", r"\bxfail\b", r"\.skip\(", r"\.only\(", r"\btest\.todo\b"]
SUPPRESS = [r"#\s*type:\s*ignore", r"@ts-ignore", r"@ts-expect-error", r"eslint-disable", r"#\s*noqa"]
LABEL, PIN_EVENT, PIN_PATH = "spec-approved", "pull_request_target", ".github/workflows/factory.yml"

def short(items, n=5):
    items = list(dict.fromkeys(items))
    return ", ".join(items[:n]) + (f" (+{len(items) - n} more)" if len(items) > n else "")

def diff_lines(patch, sign):
    return [ln[1:].strip() for ln in (patch or "").splitlines() if ln.startswith(sign)]

def i1_locked(spec, events):
    locked, sc = spec["meta"].get("locked") or [], spec["meta"].get("spec_commit")
    if not sc:
        return [Flag("I1", "fail", "the spec locks files but has no spec_commit. Next: re-shape")] if locked else []
    cmp = events.get("compare")
    if not cmp or cmp.get("status") not in ("ahead", "identical"):
        return [Flag("I1", "fail", "spec_commit not in PR history. Next: restore the branch, or re-shape")]
    cut = [Flag("I1", "raise:T2", "the compare lists 300+ files: locked files past that are unchecked")]
    cut = cut if len(cmp.get("files", [])) >= 300 else []  # the compare API lists at most 300 files
    hits = [f"{n} ({g})" for f in cmp.get("files", []) for n in names(f)
            if n != spec.get("path") and (g := fl.any_match(locked, n))]  # the spec file is guarded by I7
    return [Flag("I1", "fail", f"locked files changed after spec_commit: {short(hits)}. "
                               "Next: revert them; if a test is wrong, write it under ## Spec issues")] * bool(hits) + cut

def i1_acceptance(lock):  # A3: CI's own lock, from the base config and the PR's commits, never from the spec
    if not lock:
        return []
    if not lock.get("compare"):
        why = f"since {lock['first'][:12]}" if lock.get("first") else "250+ commits; the API lists 250"
        return [Flag("I1", "raise:T2", f"could not check the acceptance tests ({why})")]
    hits = [n for f in lock["compare"].get("files", []) for n in names(f) if fl.any_match([lock["glob"]], n)]
    return [Flag("I1", "raise:T2", f"acceptance tests changed after the commit that added them ({lock['first'][:12]}): "
                                   f"{short(hits)}. Fine for a re-shape; otherwise revert them")] * bool(hits)

def patterns(cfg, key, base):
    return re.compile("|".join(f"(?:{p})" for p in [*base, *cfg["integrity"].get(key, [])]))

def i2_test_edits(pr, cfg):
    out, nopatch, gone, skips, sups = [], [], [], [], []
    a, sk, su = patterns(cfg, "assert", ASSERT), patterns(cfg, "skip", SKIP), patterns(cfg, "suppress", SUPPRESS)
    if pr.get("files_truncated"):
        out.append(Flag("I2", "raise:T2", "more PR files than the API lists: patch unavailable"))
    for f in pr.get("files", []):
        n = f["filename"]
        if not any(fl.any_match(cfg["tests"], x) for x in names(f)) or not int(f.get("changes", 1) or 0):  # moved out too
            continue  # a pure rename has no patch and no changes
        if "patch" not in f:
            nopatch.append(n)
            continue
        added, removed = collections.Counter(diff_lines(f["patch"], "+")), collections.Counter(diff_lines(f["patch"], "-"))
        gone += [f"{n}: {ln}" for ln in removed - added if a.search(ln)]  # moved lines cancel out
        skips += [f"{n}: {ln}" for ln in added - removed if sk.search(ln)]
        sups += [f"{n}: {ln}" for ln in added - removed if su.search(ln)]
    for items, what in ((nopatch, "patch unavailable"), (gone, "removed assertions"), (skips, "added skip/only/todo"),
                        (sups, "added suppressions")):
        if items:
            out.append(Flag("I2", "raise:T2", f"{what}: {short(items, 3)}"))
    return out

def i7_approval(pr, spec, cfg, events):
    who, url = cfg["authority"]["approver"], pr.get("html_url") or f"PR #{pr['number']}"
    again = f"Next: as {who}, re-apply {LABEL} on {url}"
    if not who:
        return "set authority.approver in .factory/config.toml (a declared T3 needs an approver)"
    if not spec or not spec.get("path"):
        return "a declared T3 needs a spec file under specs_dir. Next: /factory:shape <issue>"
    if LABEL not in {lb.get("name") for lb in pr.get("labels", [])}:
        return f"no {LABEL} label. Next: as {who}, review the spec and add {LABEL} on {url}"
    evs = [e for e in events.get("label_events", []) if e.get("event") == "labeled"
           and (e.get("label") or {}).get("name") == LABEL]
    if not evs:
        return f"no {LABEL} label event. {again}"
    last = max(evs, key=lambda e: e["id"])
    by = (last.get("actor") or {}).get("login")
    if by != who or last.get("performed_via_github_app"):
        return f"{LABEL} by {by} ignored (needs {who}, not via an app). {again}"
    pins = [p["pin"] for p in events.get("pins", []) if (p.get("run") or {}).get("event") == PIN_EVENT
            and p["run"].get("path") == PIN_PATH and p["pin"].get("pr") == pr["number"]
            and p["pin"].get("by") == who and p["pin"].get("label_event_id") == last["id"]]
    if not pins:
        return f"no factory-pin artifact for the latest {LABEL} event. {again}"
    if pins[-1].get("spec") != spec["path"] or pins[-1].get("blob") != events.get("spec_blob"):
        return f"spec changed after approval. Next: {who} re-applies {LABEL} on {url}"
    return None

def check(pr, spec, cfg, events):
    files, tier, out = pr.get("files", []), events.get("tier"), []
    out += i2_test_edits(pr, cfg)
    out += i1_acceptance(events.get("acc_lock"))
    if th := [n for f in files for n in names(f) if fl.any_match(cfg["integrity"]["threshold_globs"], n)]:
        out.append(Flag("I3", "raise:T2", f"coverage/lint threshold config changed: {short(th)}"))
    if spec:
        out += i1_locked(spec, events)
        scope = spec["meta"].get("scope") or []
        if scope and tier_num(spec["meta"].get("tier")) != 0:  # T1+ with a spec; an explicit T0 spec is exempt
            outside = [f["filename"] for f in files if f["filename"] != spec.get("path")
                       and not fl.any_match(scope, f["filename"])]
            if outside:
                out.append(Flag("I4", "raise:T2", f"files outside the spec scope: {short(outside)}"))
        q = [e.get("test", "") if isinstance(e, dict) else str(e) for e in cfg["quarantine"]]
        cmds = re.findall(r"`([^`]+)`", spec["sections"].get("Acceptance checks", ""))
        if hits := [t for t in q if t and any(t in c for c in cmds)]:
            out.append(Flag("I5", "fail", f"quarantined tests used as acceptance checks: {short(hits)}. "
                                          "Next: fix the test or change the acceptance check"))
    if tier is not None and tier > 0 and not spec:
        out.append(Flag("I6", "fail", f"needs a spec. Next: /factory:shape {events.get('issue') or '<issue>'}"))
    # D23: approval at the PR, only for a declared T3; a computed raise changes merge policy, never asks for approval
    if (events.get("declared") or 0) >= 3 and spec and (msg := i7_approval(pr, spec, cfg, events)):
        out.append(Flag("I7", "fail", msg))
    return out
