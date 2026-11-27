"""Shared by the factory hooks, bin/fx and ci/ (stdlib only, Python >= 3.11)."""
import contextlib, copy, datetime, hashlib, json, os, re, shutil, signal, subprocess, time, tomllib
from functools import lru_cache
from pathlib import Path

PACKET_KEYS = ("intent", "ac", "changed", "deviations", "test_changes", "new_deps", "look_here", "not_verified", "rollback")
DEFAULTS = {
    "project": "", "quarantine": [], "branch_types": [], "setup": [],
    "tests": ["tests/**", "test/**", "**/test_*.py", "**/*_test.py", "**/*.test.*", "**/*.spec.*"],
    "acceptance_glob": "tests/acceptance/test_{issue}_*", "specs_dir": "docs/specs", "spec_name": "{issue}-{slug}.md",
    "branch": "f/{issue}-{slug}", "worktree_root": "../{project}-worktrees", "worktree_name": "{issue}-{slug}",
    "verify": {"fast_budget_s": 300, "cap_s": 600, "full_cap_s": 3600, "fast_locks": [], "full_locks": []},
    "tiers": {"docs_globs": ["docs/**", "**/*.md"], "sensitive": [], "gate_extra": []},
    "integrity": {"threshold_globs": ["**/jest.config.*", "**/vitest.config.*", "**/.eslintrc*", "**/eslint.config.*",
                                      "**/tsconfig*.json", "**/.coveragerc", "**/.nycrc*"]},
    "authority": {"approver": ""},
    "packet": {"max_lines": 40, "extra": [], "headings": dict(zip(PACKET_KEYS, (
        "## Intent", "## Acceptance checks", "## What changed", "## Deviations from spec", "## Test changes",
        "## New dependencies", "## Look here", "## Not verified", "## Rollback")))},
}
USER_DEFAULTS = {"verify_slots": 2, "lock_wait_s": 300, "session": {"build_note": ""}}
GATE_FILES = [".claude/**", ".factory/**", ".github/**", "CLAUDE.md", "**/CLAUDE.md", "**/CODEOWNERS"]
VERIFY_LINE = re.compile(r"^factory-verify: (\S+) (PASS|FAIL) (\d+(?:\.\d+)?)s\s*$", re.M)
NT = os.name == "nt"

def home():
    return Path(os.environ.get("FACTORY_HOME") or Path.home() / ".factory")

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

def merge(base, over):  # dicts merge recursively; any other value in `over` replaces the base value
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out

def load_cfg_text(text):
    return merge(DEFAULTS, tomllib.loads(text))

def user_cfg():
    p = home() / "config.toml"
    return merge(USER_DEFAULTS, tomllib.loads(p.read_text("utf-8")) if p.exists() else {})

def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return default

def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else None

def parse_issue(pattern, branch):
    rx = re.escape(pattern)
    for key, sub in (("type", "[^/]+"), ("issue", r"(?P<issue>\d+)"), ("slug", ".+")):
        rx = rx.replace(re.escape("{" + key + "}"), sub)
    m = re.fullmatch(rx, branch or "")
    return m.group("issue") if m and "issue" in m.groupdict() else None

def repo_ctx(cwd):
    root = git(cwd, "rev-parse", "--show-toplevel")
    if not root:
        return None
    root = Path(root).resolve()
    cfg_path, branch = root / ".factory" / "config.toml", git(root, "rev-parse", "--abbrev-ref", "HEAD")
    cfg = load_cfg_text(cfg_path.read_text("utf-8")) if cfg_path.exists() else None
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", git(root, "remote", "get-url", "origin") or "")
    issue = parse_issue(cfg["branch"], branch) if cfg else None
    work = root / ".factory" / "work" / issue if issue else None
    return {"root": root, "branch": branch, "repo": m.group(1) if m else None, "cfg": cfg, "issue": issue,
            "work": work, "state": load_json(work / "state.json", {}) if work else {}}

def save_state(ctx, **updates):
    ctx["state"].update(updates)
    ctx["work"].mkdir(parents=True, exist_ok=True)
    tmp = ctx["work"] / "state.json.tmp"
    tmp.write_text(json.dumps(ctx["state"], indent=2), "utf-8")
    os.replace(tmp, ctx["work"] / "state.json")

def extract_block(text):
    m = re.search(r"<!-- factory:spec -->\s*(.*?)\s*<!-- /factory:spec -->", text or "", re.S)
    return m.group(1) if m else None

def load_spec(path=None, text=None):  # -> (meta, body, sections); a spec file, or an issue body with the block
    text = Path(path).read_text("utf-8") if path is not None else (text or "")
    text = extract_block(text) or text
    m = re.match(r"\s*\+\+\+\s*\n(.*?)\n\+\+\+\s*\n?(.*)", text, re.S)
    meta, body = (tomllib.loads(m.group(1)), m.group(2)) if m else ({}, text)
    parts = re.split(r"^## +(.+?)\s*$", body, flags=re.M)
    return meta, body, {k: v.strip() for k, v in zip(parts[1::2], parts[2::2])}

def phase(ctx):  # FACTORY_PHASE, else state.json, else build iff the spec has spec_commit; None = hooks no-op
    if os.environ.get("FACTORY_PHASE") or not ctx or not ctx.get("work"):
        return os.environ.get("FACTORY_PHASE") or None
    if ctx["state"].get("phase") or not ctx["work"].is_dir():
        return ctx["state"].get("phase")
    sp = ctx["root"] / ctx["state"].get("spec_path", f".factory/work/{ctx['issue']}/spec.md")
    return "build" if sp.is_file() and load_spec(sp)[0].get("spec_commit") else "shape"

@lru_cache(maxsize=512)
def _glob_rx(pat):
    out, i = [], 0
    while i < len(pat):
        c, j = pat[i], pat.find("]", i + 2)
        if pat.startswith("**/", i):
            out.append("(?:[^/]*/)*"); i += 3
        elif pat.startswith("**", i):
            out.append(".*"); i += 2
        elif c == "[" and j > 0:
            cls = pat[i + 1:j]
            out.append("[" + ("^" + cls[1:] if cls.startswith("!") else cls).replace("\\", "\\\\") + "]"); i = j + 1
        else:
            out.append({"*": "[^/]*", "?": "[^/]"}.get(c, re.escape(c))); i += 1
    return re.compile("".join(out) + r"\Z")

def glob_match(pattern, relpath):  # `**` = any number of segments; `*`, `?` stay in one segment; `[...]`, `[!...]`
    return bool(_glob_rx(pattern).match(str(relpath).replace("\\", "/").lstrip("/")))

def any_match(globs, relpath):
    return next((g for g in globs if glob_match(g, relpath)), None)

def is_gate(relpath, cfg):
    return None if glob_match(".factory/work/**", relpath) else any_match(GATE_FILES + cfg["tiers"]["gate_extra"], relpath)

def sh():
    return shutil.which("sh") or "C:/Program Files/Git/bin/sh.exe"

def fingerprint(root):
    status = git(root, "status", "--porcelain") or ""
    return f"{git(root, 'rev-parse', 'HEAD')}:{hashlib.sha256(status.encode()).hexdigest()[:16]}"

def _lk(fd, on):  # OS lock on byte 0 (non-blocking; OSError if held elsewhere)
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, 0)
        return msvcrt.locking(fd, msvcrt.LK_NBLCK if on else msvcrt.LK_UNLCK, 1)
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB if on else fcntl.LOCK_UN)

def _try_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    with contextlib.suppress(OSError):
        _lk(fd, True)
        return fd
    os.close(fd)

@contextlib.contextmanager
def verify_locks(resources, slots, wait_s):  # yields True once 1 of `slots` slots + every resource lock are held
    d, held, deadline = home() / "locks", [], time.monotonic() + wait_s
    d.mkdir(parents=True, exist_ok=True)
    try:
        for name in [None] + sorted(set(resources)):
            cands = [d / f"slot-{n}.lock" for n in range(slots)] if name is None else [d / f"{name}.lock"]
            while (fd := next((f for f in map(_try_lock, cands) if f is not None), None)) is None:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(0.2)
            held.append(fd)
        yield True
    finally:
        for fd in held:
            _lk(fd, False)
            os.close(fd)

def _job(pid):  # Windows job holding pid's future children; MSYS fork breaks the parent chain taskkill /T walks
    import ctypes
    k, vp = ctypes.WinDLL("kernel32"), ctypes.c_void_p
    k.CreateJobObjectW.restype = k.OpenProcess.restype = vp
    job, proc = k.CreateJobObjectW(None, None), k.OpenProcess(0x0101, False, pid)  # PROCESS_TERMINATE | SET_QUOTA
    ok = bool(job and proc and k.AssignProcessToJobObject(vp(job), vp(proc)))
    if proc:
        k.CloseHandle(vp(proc))
    return (lambda: k.TerminateJobObject(vp(job), 1)) if ok else None

def run_tree(cmd, cwd, timeout):  # own process group; on timeout kill the whole tree -> (rc, output, timed_out)
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if NT else 0, start_new_session=not NT)
    job = _job(p.pid) if NT else None
    try:
        out, timed_out = p.communicate(timeout=timeout)[0], False
    except subprocess.TimeoutExpired:
        if os.name == "nt":  # never os.kill(pid, 0) on Windows: it terminates the process
            if job:
                job()
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            os.killpg(p.pid, signal.SIGKILL)
        out, timed_out = p.communicate()[0], True
    return p.returncode, out.decode("utf-8", "replace"), timed_out

def tail(text, lines=60, chars=8000):
    return "\n".join([ln for ln in text.splitlines() if ln.strip()][-lines:])[-chars:]

def run_verify(ctx, mode):  # -> {ok, failed, tail, lines, reason, seconds, over_budget}; writes last_verify
    v, u, root = ctx["cfg"]["verify"], user_cfg(), ctx["root"]
    fp, t0, script = fingerprint(root), time.monotonic(), root / ".factory" / "verify"
    res = {"ok": False, "failed": ["verify-script"], "tail": f"No {script}.", "reason": "no-verify-script", "lines": []}
    if script.is_file():
        locks = v[f"{mode}_locks"] + (["full"] if mode == "full" else [])
        with verify_locks(locks, int(u["verify_slots"]), float(u["lock_wait_s"])) as got:
            if not got:
                res.update(failed=["lock"], tail=f"Waited {u['lock_wait_s']}s for verify locks {locks}.", reason="lock-timeout")
            else:
                rc, out, timed_out = run_tree([sh(), str(script), f"--{mode}"], root, v["cap_s" if mode == "fast" else "full_cap_s"])
                checks = VERIFY_LINE.findall(out)
                failed = [n for n, r, _ in checks if r == "FAIL"] or ([] if rc == 0 and not timed_out else ["verify"])
                ok = rc == 0 and not timed_out and not failed
                res = {"ok": ok, "failed": failed, "tail": tail(out), "lines": [f"factory-verify: {' '.join(c)}s" for c in checks],
                       "reason": "ok" if ok else "timeout" if timed_out else "fail"}
    res["seconds"] = round(time.monotonic() - t0, 1)
    res["over_budget"] = mode == "fast" and res["ok"] and res["seconds"] > v["fast_budget_s"]
    if res["over_budget"]:
        log("verify", ctx, decision="over_budget", reason="ok", verify_ok=True, seconds=res["seconds"])
    if ctx.get("work"):
        save_state(ctx, last_verify={"mode": mode, "fingerprint": fp, "at": now(),
                                     **{k: res[k] for k in ("ok", "over_budget", "seconds", "failed", "reason", "tail")}})
    return res

def log(hook, ctx=None, **fields):
    ctx = ctx or {}
    rec = {"ts": now(), "session_id": None, "repo": ctx.get("repo"), "issue": ctx.get("issue"),
           "phase": phase(ctx) if ctx else None, "hook": hook, "decision": None, "reason": None, "verify_ok": None, **fields}
    p = home() / "logs" / "hooks.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")

def packet_check(text, cfg, tier):  # -> list of problems (empty = ok); tier is an int 0..3
    pk, lines = cfg["packet"], text.splitlines()
    present = {ln.strip() for ln in lines}
    probs = [f"missing heading {h!r}" for h in dict.fromkeys([*pk["headings"].values(), *pk["extra"]]) if h and h not in present]
    if len(lines) > pk["max_lines"]:
        probs.append(f"{len(lines)} lines > max {pk['max_lines']}")
    nv = pk["headings"]["not_verified"]
    sec = re.search(rf"^{re.escape(nv)}[ \t]*$(.*?)(?=^#{{1,3}} |\Z)", text, re.M | re.S)
    body = sec.group(1).strip() if sec else ""
    if "not verified" not in nv.lower():  # e.g. "## Follow-ups": needs a "Not verified: <text>" line
        m = re.search(r"^Not verified:(.*)$", body, re.M)
        body = m.group(1).strip() if m else ""
    if tier >= 1 and not body:
        probs.append(f"'Not verified' is empty (under {nv!r})")
    if tier >= 2 and not re.search(r"^Review: /code-review \w+, \d+ findings", text, re.M):
        probs.append("T2+ needs a line 'Review: /code-review <level>, <n> findings'")
    return probs
