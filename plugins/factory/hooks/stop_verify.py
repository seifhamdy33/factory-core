#!/usr/bin/env python3
"""Stop hook: a build turn can't end while `.factory/verify --fast` is red (spec: Hooks > stop_verify.py).
Exit 2 + stderr keeps the agent working. A new `## Blocker` in the notes file allows one stop (the question channel)."""
import hashlib, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import factory_lib as fl  # noqa: E402

HOOK = "stop_verify"
BLOCKER_ASK = ("Stop iterating. Add a `## Blocker` section to {notes}: what fails, what you tried, your best "
               "hypothesis, what you need from the owner. Post the same text as an issue comment, then end your turn.")
NEXT_FIX = "Next: fix and end your turn again, or add `## Blocker` to {notes} if you're stuck."
NEXT = {
    "lock-timeout": "Next: end your turn; verify will retry.",
    "no-verify-script": "Next: the overlay needs a .factory/verify script (template: plugins/factory/templates/verify.sh "
                        "in factory-core). You can't add it in a build; say so under `## Blocker` in {notes}.",
    "timeout": "Next: find what hangs or is slow (verify --fast is killed at [verify] cap_s), fix it and end your turn.",
}

def notes_rel(ctx):
    p = fl.notes_path(ctx)
    return p.relative_to(ctx["root"]).as_posix() if p.is_relative_to(ctx["root"]) else str(p)

def blocker_hash(p):
    text = p.read_text("utf-8", errors="replace") if p.is_file() else ""
    m = fl.BLOCKER.search(text)
    return hashlib.sha256(m.group(1).strip().encode()).hexdigest()[:16] if m else None

def strike(ctx, sig):
    """Count consecutive identical failures in state.json `stop`. -> True at the 3rd (ask for a Blocker)."""
    st = dict(ctx["state"].get("stop") or {})
    st["count"] = st.get("count", 0) + 1 if st.get("sig") == sig else 1
    st["sig"] = sig
    if st["count"] >= 3:
        st["blocker_requested"] = True
    fl.save_state(ctx, stop=st)
    return st["count"] >= 3

def run(data, box):
    ctx = fl.repo_ctx(data.get("cwd") or os.getcwd())
    if not ctx or not ctx["work"] or fl.phase(ctx) != "build":
        return 0, ""
    box["ctx"] = ctx
    sid, consumed = data.get("session_id"), (ctx["state"].get("stop") or {}).get("blocker_consumed")
    bh = blocker_hash(fl.notes_path(ctx))
    if bh and bh != consumed:
        fl.save_state(ctx, stop={"blocker_consumed": bh})
        fl.log(HOOK, ctx, session_id=sid, decision="allow", reason="blocker")
        return 0, ""
    lv = ctx["state"].get("last_verify") or {}
    reused = bool(data.get("stop_hook_active") and lv.get("mode") == "fast"  # a lock-timeout never ran: retry it
                  and lv.get("reason") not in (None, "lock-timeout") and lv.get("fingerprint") == fl.fingerprint(ctx["root"]))
    res = lv if reused else fl.run_verify(ctx, "fast")
    if res["ok"]:
        fl.save_state(ctx, stop={"blocker_consumed": consumed})
        fl.log(HOOK, ctx, session_id=sid, decision="allow", reason="green", verify_ok=True, reused=reused,
               over_budget=res.get("over_budget"))
        return 0, ""
    sig = hashlib.sha256(json.dumps([sorted(res["failed"]), res["reason"]]).encode()).hexdigest()[:16]
    names, notes = ", ".join(res["failed"]), notes_rel(ctx)
    if strike(ctx, sig):
        msg = BLOCKER_ASK.format(notes=notes) + f"\nStill failing ({res['reason']}): {names}."
    else:
        msg = (f"factory: verify --fast is red ({res['reason']}). Failing: {names}\n"
               f"{fl.tail(res.get('tail') or '')}\n") + NEXT.get(res["reason"], NEXT_FIX).format(notes=notes)
    fl.log(HOOK, ctx, session_id=sid, decision="block", reason=res["reason"], verify_ok=False, failed=res["failed"],
           count=ctx["state"]["stop"]["count"], reused=reused)
    return 2, msg

def build_near(cwd):
    """An error before ctx loads (e.g. bad config.toml): is cwd in a worktree whose state.json says build?"""
    for d in [Path(cwd), *Path(cwd).parents]:
        for s in (d / ".factory" / "work").glob("*/state.json"):
            try:
                if json.loads(s.read_text("utf-8")).get("phase") == "build":
                    return True
            except (OSError, ValueError):
                return True  # unreadable state in a factory worktree: treat as build
        if (d / ".git").exists():
            return False
    return False

def on_error(err, box, data):
    """Never silently allow an active build: block, and let the 3-strike rule turn repeats into a Blocker ask."""
    ctx = box.get("ctx")
    if not ctx and os.environ.get("FACTORY_PHASE") != "build" and not build_near(data.get("cwd") or os.getcwd()):
        return 0, ""
    notes = "the notes file"
    try:
        notes = notes_rel(ctx) if ctx else notes
    except Exception:  # noqa: BLE001 (a bad user config: still block)
        pass
    msg = f"factory hook error: {type(err).__name__}: {err}\n" + NEXT_FIX.format(notes=notes)
    try:
        if ctx and strike(ctx, "hook-error"):
            msg = BLOCKER_ASK.format(notes=notes) + f"\n{msg}"
        fl.log(HOOK, ctx, session_id=data.get("session_id"), decision="block", reason="hook-error", verify_ok=False,
               error=str(err))
    except Exception:  # noqa: BLE001 (state or log unwritable: still block)
        pass
    return 2, msg

def handle(raw):
    box, data = {}, {}
    try:
        data = json.loads(raw or "{}")
        return run(data, box)
    except Exception as err:  # noqa: BLE001
        return on_error(err, box, data if isinstance(data, dict) else {})

if __name__ == "__main__":
    code, msg = handle(sys.stdin.buffer.read().decode("utf-8", "replace"))
    if msg:
        sys.stderr.buffer.write(msg.encode("utf-8") + b"\n")
    sys.exit(code)
