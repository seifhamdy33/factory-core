#!/usr/bin/env python3
"""PreToolUse (Write, Edit, NotebookEdit): in phase build, deny edits to locked files (spec: Hooks > test_lock.py).
Bash can still bypass this; CI integrity (I1, I7) is the real check."""
import json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import factory_lib as fl  # noqa: E402

MSG = ("Acceptance tests are locked ({path} matches {glob}). If you believe a test is wrong, write it under "
       "`## Spec issues` in {notes} and stop.")

def locked_globs(ctx):
    """-> (locked globs, spec scope). The notes file and packet.md stay editable."""
    w, st = f".factory/work/{ctx['issue']}", ctx["state"]
    spec_path = st.get("spec_path") or f"{w}/spec.md"
    meta = fl.load_spec(ctx["root"] / spec_path)[0] if (ctx["root"] / spec_path).is_file() else {}
    return [*meta.get("locked", []), ctx["cfg"]["acceptance_glob"].replace("{issue}", ctx["issue"]), spec_path,
            ".factory/verify", ".factory/config.toml", f"{w}/spec.md", f"{w}/intent.md", f"{w}/state.json"], \
        meta.get("scope", [])

def run(data, box):
    cwd = data.get("cwd") or os.getcwd()
    ctx = fl.repo_ctx(cwd)
    if not ctx or not ctx["work"] or fl.phase(ctx) != "build":
        return 0, ""
    box["ctx"] = ctx
    ti = data.get("tool_input") or {}
    target = ti.get("file_path") or ti.get("notebook_path")
    if not target:
        return 0, ""
    path = (Path(cwd) / target).resolve()  # an absolute target replaces cwd; normalizes C:\ and ..
    home = fl.home().resolve()
    if path == home or path.is_relative_to(home):
        hit, shown = "~/.factory/**", str(path)
    elif not path.is_relative_to(ctx["root"]):
        return 0, ""
    else:
        shown = path.relative_to(ctx["root"]).as_posix()
        globs, scope = locked_globs(ctx)
        hit = fl.any_match(globs, shown) or (None if fl.any_match(scope, shown) else fl.is_gate(shown, ctx["cfg"]))
        if not hit:
            return 0, ""
    fl.log("test_lock", ctx, session_id=data.get("session_id"), decision="deny", reason=hit, path=shown,
           tool=data.get("tool_name"))
    return 2, MSG.format(path=shown, glob=hit, notes=p.relative_to(ctx["root"]).as_posix() if (p := fl.notes_path(ctx)).is_relative_to(ctx["root"]) else str(p))

def handle(raw):
    box = {}
    try:
        return run(json.loads(raw or "{}"), box)
    except Exception as err:  # noqa: BLE001 (fail closed only when the session is an active build)
        if not box.get("ctx") and os.environ.get("FACTORY_PHASE") != "build":
            return 0, ""
        return 2, f"factory hook error: {type(err).__name__}: {err}. The edit was not checked against the test lock."

if __name__ == "__main__":
    code, msg = handle(sys.stdin.buffer.read().decode("utf-8", "replace"))
    if msg:
        sys.stderr.buffer.write(msg.encode("utf-8") + b"\n")
    sys.exit(code)
