"""factory/tier: a pure function of (PR files, config, integrity flags, declared tier) -> (tier, reasons).
Spec: CI > ci/tier.py. Files are GitHub "list PR files" objects (filename, previous_filename, additions, deletions)."""
import re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "factory" / "lib"))
import factory_lib as fl  # noqa: E402

# Phase 1 stand-in for deps.py: any lockfile or manifest change is T2. An overlay's [tiers] deps globs are added.
DEP_GLOBS = ["**/" + n for n in (
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb",
    "pyproject.toml", "poetry.lock", "uv.lock", "Pipfile", "Pipfile.lock", "requirements*.txt", "setup.py", "setup.cfg",
    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock", "Gemfile", "Gemfile.lock", "composer.json", "composer.lock")]
NOT_EVALUATED = "coverage of changed lines is not evaluated here (the merger checks it, G1)"
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

def parse_lcov(text):
    """LCOV text -> {source path: {line: hits}} (SF: and DA: records only)."""
    out, cur = {}, None
    for ln in (text or "").splitlines():
        if ln.startswith("SF:"):
            cur = out.setdefault(ln[3:].strip().replace("\\", "/"), {})
        elif ln.startswith("DA:") and cur is not None:
            n, hits = (ln[3:].split(",") + ["0"])[:2]
            if n.strip().isdigit():
                cur[int(n)] = cur.get(int(n), 0) + int(hits) if hits.strip().lstrip("-").isdigit() else 0
    return out

def added_lines(patch):
    """A unified diff -> the new-file numbers of its added non-blank lines."""
    out, n = [], None
    for ln in (patch or "").splitlines():
        if m := HUNK.match(ln):
            n = int(m[1])
        elif n is not None and not ln.startswith("\\"):
            if ln.startswith("+") and ln[1:].strip():
                out.append(n)
            n += not ln.startswith("-")
    return out

def report_root(files, report):
    """The checkout prefix of the report's paths ("" when relative): the shortest prefix that turns a report path into
    a changed file's path and starts every report path. A same-named file deeper down never wins; None if no match."""
    roots = {k[:-len(n)] for f in files if (n := f.get("filename")) for k in report if k == n or k.endswith("/" + n)}
    return min((r for r in roots if all(k.startswith(r) for k in report)), key=len, default=None)

def changed_coverage(files, cfg, report):
    """-> (covered, counted, note). Code files only (not docs, tests or gate files). A changed code file missing from
    the report counts as uncovered when the report covers its extension; a file with no patch is unknown (None)."""
    core_docs, cov, counted = fl.DEFAULTS["tiers"]["docs_globs"], 0, 0
    exts, root = {Path(k).suffix for k in report}, report_root(files, report)
    for f in files:
        n = f.get("filename") or ""
        if f.get("status") == "removed" or fl.is_gate(n, cfg) or fl.any_match(cfg["tests"], n) or (
                fl.any_match(core_docs, n) and fl.any_match(cfg["tiers"]["docs_globs"], n)):
            continue
        if report and Path(n).suffix not in exts:  # e.g. data or config: the report doesn't measure it
            continue
        if "patch" not in f and int(f.get("additions") or 0):
            return None, None, f"{n} has no patch in the API (too large or binary)"
        if not (added := added_lines(f.get("patch"))):
            continue
        if not report:  # changed code and no line data: missing
            return None, None, None
        hits = report.get(root + n) if root is not None else None
        lines = [x for x in added if hits is None or x in hits]
        counted, cov = counted + len(lines), cov + sum(1 for x in lines if hits and hits[x] > 0)
    return cov, counted, f"changed-line coverage {cov}/{counted}"

def tier_num(value):
    """'T2', 't2', 2 or '2' -> 2; None or junk -> None."""
    try:
        n = int(str(value).strip().lstrip("Tt"))
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 3 else None  # 'T4' / '-1' are junk, not a tier

def names(f):
    return [n for n in (f.get("filename"), f.get("previous_filename")) if n]

def lines(f):
    return int(f.get("additions") or 0) + int(f.get("deletions") or 0)

def compute(files, cfg, flags=(), declared=None, coverage=None):
    """-> (tier 0..3, reasons). reasons[0] is the deciding reason; the last one is the coverage note. coverage: None
    (not evaluated: the gates), an LCOV report from parse_lcov, or a string saying why there is none (G1: T2)."""
    t, cands, note = cfg["tiers"], [], NOT_EVALUATED
    if coverage is not None:
        rep = coverage if isinstance(coverage, dict) else {}  # a string says why there is no report
        cov, counted, note = changed_coverage(files, cfg, rep)
        note = note or (coverage if isinstance(coverage, str) else "the coverage report has no line data")
        least = cfg["coverage"]["changed_lines_min"]
        if cov is None or (counted and 100 * cov < least * counted):
            note = f"{note}: below {least:g}%" if cov is not None else f"no changed-line coverage: {note}"
            cands.append((2, note))
    for f in files:
        for n in names(f):  # a rename counts under both names (moving a gate file away is still a gate change)
            if g := fl.is_gate(n, cfg):
                cands.append((3, f"gate file {n} ({g})"))
            if s := fl.any_match(t["sensitive"], n):
                cands.append((3, f"sensitive path {n} ({s})"))
            if d := fl.any_match([*DEP_GLOBS, *t.get("deps", [])], n):
                cands.append((2, f"dependency manifest {n} changed ({d})"))
    for flag in flags:
        if flag.kind.startswith("raise:T"):
            cands.append((int(flag.kind[-1]), f"integrity {flag.code}: {flag.msg}"))
    core_docs = fl.DEFAULTS["tiers"]["docs_globs"]  # G5: an overlay's docs_globs can only narrow the core's
    docs = [f for f in files if all(fl.any_match(core_docs, n) and fl.any_match(t["docs_globs"], n) for n in names(f))]
    code, total = sum(lines(f) for f in files if f not in docs), sum(lines(f) for f in files)  # size is information only (D23)
    if cands:
        computed = max(c for c, _ in cands)
        why = list(dict.fromkeys(r for c, r in sorted(cands, key=lambda c: -c[0])))
    elif len(docs) == len(files):
        computed, why = 0, [f"docs only, {total} lines"]
    else:
        computed, why = 1, [f"{len(files)} files, {code} code lines"]
    reasons = [f"computed T{computed}: {why[0]}", *why[1:]]
    dec = tier_num(declared)
    if dec is not None and dec > computed:
        reasons.insert(0, f"declared T{dec} (issue label or spec) > computed T{computed}")
    return max(computed, dec or 0), reasons + [note]
