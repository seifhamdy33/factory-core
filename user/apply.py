#!/usr/bin/env python3
"""Apply factory user config (spec: User config): union the deny list into ~/.claude/settings.json (backup first,
only when something changes), set `model:` in the listed agents' frontmatter, create ~/.factory/config.toml if
missing. Keys under "check" are reported, never written. --check changes nothing and exits 1 on any drift."""
import datetime, json, os, re, shutil, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "plugins" / "factory" / "lib"))
import factory_lib as fl  # noqa: E402

def union(dst, src, path=""):  # -> list of additions; lists gain missing items, dicts merge, nothing is removed
    added = []
    for k, v in src.items():
        if isinstance(v, dict):
            added += union(dst.setdefault(k, {}), v, f"{path}{k}.")
        elif isinstance(v, list):
            have = dst.setdefault(k, [])
            added += [f"{path}{k} += {x}" for x in v if x not in have]
            have += [x for x in v if x not in have]
        elif k not in dst:
            dst[k] = v
            added.append(f"{path}{k} = {v}")
    return added

def mismatches(have, want, path=""):
    out = []
    for k, v in want.items():
        got = have.get(k) if isinstance(have, dict) else None
        out += mismatches(got, v, f"{path}{k}.") if isinstance(v, dict) else [] if got == v else [f"{path}{k} is {got!r}, want {v!r}"]
    return out

def set_agent_model(text, model):  # -> new text, or None when the file has no frontmatter
    m = re.match(r"---\r?\n(.*?)\r?\n---", text, re.S)
    if not m:
        return None
    front = re.sub(r"(?m)^model:.*$", f"model: {model}", m.group(1)) if re.search(r"(?m)^model:", m.group(1)) \
        else m.group(1) + f"\nmodel: {model}"
    return text[:m.start(1)] + front + text[m.end(1):]

def main(argv=None):
    check = "--check" in (argv if argv is not None else sys.argv[1:])
    frag = json.loads((HERE / "settings.fragment.json").read_text("utf-8"))
    claude = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    sp = claude / "settings.json"
    settings = json.loads(sp.read_text("utf-8")) if sp.exists() else {}
    added = union(settings, frag["apply"])
    changes, drift = list(added), mismatches(settings, frag["check"])
    agents = []
    for name, want in frag["agents"].items():
        ap = claude / "agents" / f"{name}.md"
        new = set_agent_model(ap.read_text("utf-8"), want["model"]) if ap.exists() else None
        if new is None:
            drift.append(f"{ap} is missing or has no frontmatter")
        elif new != ap.read_text("utf-8"):
            agents.append((ap, new))
            changes.append(f"{ap}: model: {want['model']}")
    cfg = fl.home() / "config.toml"
    if not cfg.exists():
        changes.append(f"create {cfg} from user/factory.toml")
    if check:
        print("\n".join(f"drift: {x}" for x in changes + drift) or "factory user config: no drift")
        return 1 if changes or drift else 0
    if added:
        if sp.exists():
            shutil.copy2(sp, sp.with_name(f"settings.json.bak-factory-{datetime.datetime.now():%Y%m%d-%H%M%S}"))
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", "utf-8", newline="")
    for ap, new in agents:
        ap.write_text(new, "utf-8", newline="")
    if not cfg.exists():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HERE / "factory.toml", cfg)
    print("\n".join([f"applied: {x}" for x in changes] + [f"check only (not changed): {x}" for x in drift]) or "nothing to do")
    return 0

if __name__ == "__main__":
    sys.exit(main())
