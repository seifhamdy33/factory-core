"""factory gates (spec: CI > ci/gates.py): GitHub API glue around integrity.py and tier.py, run from the base
branch's pull_request_target workflow. It never checks out or runs PR code, and it escapes every PR-controlled
string it echoes. Mode: a `labeled` event with spec-approved runs pin, then gates; every other event runs gates."""
import base64, io, json, os, re, sys, urllib.error, urllib.parse, urllib.request, zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import integrity, tier  # noqa: E402
from integrity import LABEL, PIN_EVENT, PIN_PATH, fl  # noqa: E402

MARKER, BOT = "<!-- factory:gates -->", "github-actions[bot]"
CLOSES = re.compile(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\b")

class Api:
    def __init__(self, repo, token, root="https://api.github.com"):
        self.repo, self.token, self.root = repo, token, root  # 3xx is surfaced, so the token never goes to another host
        self.opener = urllib.request.build_opener(type("NoRedirect", (urllib.request.HTTPRedirectHandler,),
                                                       {"redirect_request": lambda *a, **k: None}))

    def call(self, method, path, body=None):
        """-> (bytes or None, headers). 404 -> (None, {}); 3xx -> (None, headers); other errors end the job."""
        req = urllib.request.Request(f"{self.root}/repos/{self.repo}/{path}", method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {self.token}", "User-Agent": "factory-gates",
                                              "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with self.opener.open(req, timeout=30) as r:
                return r.read(), r.headers
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                return None, e.headers
            if e.code == 404:
                return None, {}
            raise SystemExit(f"factory gates: GitHub API {method} {path} -> {e.code}: {e.read()[:300]!r}") from None

    def get(self, path):
        data = self.call("GET", path)[0]
        return json.loads(data) if data else None

    def get_all(self, path, key=None, pages=30):
        out, sep = [], "&" if "?" in path else "?"
        for page in range(1, pages + 1):
            got = self.get(f"{path}{sep}per_page=100&page={page}") or []
            batch = (got.get(key) or []) if isinstance(got, dict) else got
            out += batch
            if len(batch) < 100:
                break
        return out

    def download(self, path):
        loc = (self.call("GET", path)[1] or {}).get("Location")
        if not loc:
            return None
        with urllib.request.urlopen(urllib.request.Request(loc, headers={"User-Agent": "factory-gates"}), timeout=60) as r:
            return r.read()  # the signed blob URL gets no Authorization header

def q(path):
    return urllib.parse.quote(path, safe="/")

def text_of(content):
    return base64.b64decode(content["content"]).decode("utf-8", "replace") if content and "content" in content else None

def esc(s):
    """PR-controlled text in comments: no HTML comments (no forged markers), no @mentions."""
    return str(s).replace("<!--", "&lt;!--").replace("-->", "--&gt;").replace("@", "@​")

def issue_of(pr):
    ms = CLOSES.findall(pr.get("body") or "")  # the last one: fx ship appends `Closes #n` after the packet
    return int(ms[-1]) if ms else None

def load_cfg(api, pr, allow_head=True):
    for ref in (pr["base"]["sha"], pr["head"]["sha"] if allow_head else None):
        text = text_of(api.get(f"contents/.factory/config.toml?ref={ref}")) if ref else None
        if text is not None:
            note = [] if ref == pr["base"]["sha"] else ["config read from the PR head: the base branch has none "
                                                        "(onboarding PR; a Phase 2 must-fix, D15)"]
            return fl.load_cfg_text(text), note
    return fl.load_cfg_text(""), ["no .factory/config.toml on the base branch: core defaults used"]

def find_spec(api, cfg, files, issue, ref):
    """-> (spec, blob): the PR's .md under specs_dir, read at `ref`, whose frontmatter id == issue. shape commits a
    T2/T3 spec on the slice's own branch, so it is always among the PR files."""
    d = cfg["specs_dir"].strip("/") + "/"
    for p in [f["filename"] for f in files if f.get("status") != "removed"]:
        if not (p.startswith(d) and p.endswith(".md")):
            continue
        c = api.get(f"contents/{q(p)}?ref={ref}")
        try:
            meta, _, secs = fl.load_spec(text=text_of(c) or "")
        except ValueError:  # tomllib.TOMLDecodeError: not a factory spec
            continue
        if c and issue is not None and str(meta.get("id")) == str(issue):  # no closing keyword: no spec
            return {"meta": meta, "sections": secs, "path": p, "issue": issue}, c.get("sha")
    return None, None

def collect_pins(api, extra=()):
    """factory-pin artifacts from base-branch pull_request_target runs (filtered again in integrity), oldest first."""
    pins = []
    for a in api.get_all("actions/artifacts?name=factory-pin", key="artifacts", pages=3):
        run = api.get(f"actions/runs/{(a.get('workflow_run') or {}).get('id')}") or {}
        if a.get("expired") or run.get("event") != PIN_EVENT or run.get("path") != PIN_PATH:
            continue
        try:
            pin = json.loads(zipfile.ZipFile(io.BytesIO(api.download(f"actions/artifacts/{a['id']}/zip"))).read("pin.json"))
        except (TypeError, KeyError, ValueError, zipfile.BadZipFile):
            continue
        pins.append({"run": {"id": run.get("id"), "event": run["event"], "path": run["path"]}, "pin": pin})
    return sorted(pins, key=lambda p: p["run"]["id"] or 0) + list(extra)

def pin_mode(api, ev, env, notes):
    """-> (pin.json path, fresh pin) or None. Reads the spec at the labelled head, not the current one (TOCTOU)."""
    pr, sender = ev["pull_request"], (ev.get("sender") or {}).get("login")
    cfg, cfg_notes = load_cfg(api, pr, allow_head=False)  # a PR must not pick its own approver
    who, n, head = cfg["authority"]["approver"], pr["number"], pr["head"]["sha"]
    if cfg_notes or not who or sender != who:
        notes.append(f"{LABEL} by {sender} ignored. Next: {who or 'the approver (set authority.approver)'} applies the label.")
        return None
    spec, blob = find_spec(api, cfg, api.get_all(f"pulls/{n}/files"), issue_of(pr), head)
    evs = [e["id"] for e in api.get_all(f"issues/{n}/events") if e.get("event") == "labeled"
           and (e.get("label") or {}).get("name") == LABEL and (e.get("actor") or {}).get("login") == sender]
    if not spec or not evs:
        notes.append(f"{LABEL} applied, but no spec file under specs_dir at {head[:12]}. Next: /factory:shape")
        return None
    pin = {"pr": n, "head": head, "spec": spec["path"], "blob": blob, "by": sender, "label_event_id": max(evs)}
    path = Path(env.get("RUNNER_TEMP") or ".") / "factory-pin" / "pin.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pin), "utf-8")
    notes.append(f"Spec approval pinned: {spec['path']} (blob {str(blob)[:12]}) at {head[:12]} by {sender}. "
                 "This comment is informational; only the pin artifact counts.")
    wf = env.get("GITHUB_WORKFLOW_REF", "").split("@")[0].split("/", 2)[-1]
    return path, {"run": {"id": int(env.get("GITHUB_RUN_ID") or 0), "event": env.get("GITHUB_EVENT_NAME"), "path": wf},
                  "pin": pin}

def comment_body(t, reasons, flags, notes):
    out = [f"### factory gates (report-only): T{t}", *[f"- {r}" for r in reasons]]
    fails = [f for f in flags if f.kind == "fail"]
    out += ["", f"**Integrity:** {'FAIL' if fails else 'ok'} ({len(fails)} failing, {len(flags) - len(fails)} raising)"]
    out += [f"- {f.code} {f.kind}: {f.msg}" for f in sorted(flags, key=lambda f: f.kind != "fail")]
    out += ["", "**Notes:**", *[f"- {n}" for n in notes]] if notes else []
    return MARKER + "\n" + esc("\n".join(out))

def gates(api, env, ev, extra_pin=None, notes=()):
    n = ev["pull_request"]["number"]
    pr = api.get(f"pulls/{n}")
    cfg, cfg_notes = load_cfg(api, pr)
    files = api.get_all(f"pulls/{n}/files")
    pr = pr | {"files": files, "files_truncated": len(files) >= 3000 or int(pr.get("changed_files") or 0) > len(files)}
    issue_n, notes = issue_of(pr), [*notes, *cfg_notes]
    issue = api.get(f"issues/{issue_n}") if issue_n else None
    spec, blob = find_spec(api, cfg, files, issue_n, pr["head"]["sha"]) if issue_n else (None, None)
    if not spec and issue and fl.extract_block(issue.get("body")):
        try:
            meta, _, secs = fl.load_spec(text=issue["body"])
            spec = {"meta": meta, "sections": secs, "path": None, "issue": issue_n}
        except ValueError as e:
            notes.append(f"the issue's factory:spec block has invalid TOML frontmatter: {e}")
    sc = spec and spec["meta"].get("spec_commit")
    labels = [lb["name"] for lb in (issue or {}).get("labels", []) if lb["name"].startswith("tier:")]
    declared = max([tier.tier_num(lb[5:]) or 0 for lb in labels] + [tier.tier_num(spec and spec["meta"].get("tier")) or 0])
    events = {"tier": None, "declared": declared, "issue": issue_n, "spec_blob": blob,
              "compare": api.get(f"compare/{q(sc)}...{pr['head']['sha']}") if sc else None}
    t, reasons = tier.compute(files, cfg, integrity.check(pr, spec, cfg, events), declared)
    if declared >= 3:  # only I7 reads these (D23)
        events |= {"label_events": api.get_all(f"issues/{n}/events"),
                   "pins": collect_pins(api, [extra_pin] if extra_pin else [])}
    flags = integrity.check(pr, spec, cfg, events | {"tier": t})
    head, fails = pr["head"]["sha"], [f.code for f in flags if f.kind == "fail"]
    url = f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{api.repo}/actions/runs/{env.get('GITHUB_RUN_ID', '')}"
    for ctx, state, desc in (("factory/integrity", "failure" if fails else "success",
                              f"failing: {', '.join(fails)}" if fails else f"ok ({len(flags)} raising flags)"),
                             ("factory/tier", "success", f"T{t}: {reasons[0]}")):
        api.call("POST", f"statuses/{head}", {"state": state, "context": ctx, "description": desc[:140], "target_url": url})
    for lb in pr.get("labels", []):
        if lb["name"].startswith("tier:") and lb["name"] != f"tier:T{t}":
            api.call("DELETE", f"issues/{n}/labels/{q(lb['name'])}")
    if f"tier:T{t}" not in {lb["name"] for lb in pr.get("labels", [])}:
        api.call("POST", f"issues/{n}/labels", {"labels": [f"tier:T{t}"]})
    mine = [c for c in api.get_all(f"issues/{n}/comments") if (c.get("user") or {}).get("login") == BOT
            and (c.get("body") or "").startswith(MARKER)]
    body = {"body": comment_body(t, reasons, flags, notes)}
    api.call("PATCH", f"issues/comments/{mine[0]['id']}", body) if mine else api.call("POST", f"issues/{n}/comments", body)
    return t, flags

def main(env=os.environ, api=None):
    ev = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text("utf-8"))
    api, notes, extra = api or Api(env["GITHUB_REPOSITORY"], env["GH_TOKEN"]), [], None
    if ev.get("action") == "labeled" and (ev.get("label") or {}).get("name") == LABEL and (res := pin_mode(api, ev, env, notes)):
        path, extra = res
        with open(env.get("GITHUB_OUTPUT") or os.devnull, "a", encoding="utf-8") as f:
            f.write(f"pin=true\npin_path={path}\n")
    t, flags = gates(api, env, ev, extra, notes)
    print(f"factory gates: T{t}; flags: {', '.join(f'{f.code}:{f.kind}' for f in flags) or 'none'}")

if __name__ == "__main__":
    main()
