"""factory/tier: a pure function of (PR files, config, integrity flags, declared tier) -> (tier, reasons).
Spec: CI > ci/tier.py. Files are GitHub "list PR files" objects (filename, previous_filename, additions, deletions)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "factory" / "lib"))
import factory_lib as fl  # noqa: E402

# Phase 1 stand-in for deps.py: any lockfile or manifest change is T2.
DEP_GLOBS = ["**/" + n for n in (
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb",
    "pyproject.toml", "poetry.lock", "uv.lock", "Pipfile", "Pipfile.lock", "requirements*.txt", "setup.py", "setup.cfg",
    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock", "Gemfile", "Gemfile.lock", "composer.json", "composer.lock")]
NOT_EVALUATED = "coverage of changed lines is not evaluated in Phase 1"

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

def compute(files, cfg, flags=(), declared=None):
    """-> (tier 0..3, reasons). reasons[0] is the deciding reason; the last one is the coverage note."""
    t, cands = cfg["tiers"], []
    for f in files:
        for n in names(f):  # a rename counts under both names (moving a gate file away is still a gate change)
            if g := fl.is_gate(n, cfg):
                cands.append((3, f"gate file {n} ({g})"))
            if s := fl.any_match(t["sensitive"], n):
                cands.append((3, f"sensitive path {n} ({s})"))
            if d := fl.any_match(DEP_GLOBS, n):
                cands.append((2, f"dependency manifest {n} changed ({d})"))
    for flag in flags:
        if flag.kind.startswith("raise:T"):
            cands.append((int(flag.kind[-1]), f"integrity {flag.code}: {flag.msg}"))
    docs = [f for f in files if all(fl.any_match(t["docs_globs"], n) for n in names(f))]
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
    return max(computed, dec or 0), reasons + [NOT_EVALUATED]
