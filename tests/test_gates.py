import base64, contextlib, copy, http.server, io, json, re, tempfile, threading, unittest, zipfile
from pathlib import Path

from tests.helpers import CORE, fixture
import gates  # noqa: E402

HEAD = "head0000000000000000000000000000000000aa"
BASE = "base0000000000000000000000000000000000bb"
SPEC_PATH = "docs/specs/7-add-thing.md"
CFG = 'project = "x"\n[authority]\napprover = "the-approver"\n'
SPEC = ('+++\nid = 7\ntier = 2\nscope = ["src/**", "tests/**"]\nlocked = ["tests/acceptance/**"]\nspec_commit = "sc1"\n'
        '+++\n## Goal\nx\n\n## Acceptance checks\n- AC1: works → `pytest tests/acceptance`\n')


def b64(text):
    return {"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"}


def pin_zip(pin):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("pin.json", json.dumps(pin))
    return buf.getvalue()


class FakeApi:
    """Routes: path -> JSON (or bytes for downloads). Records every write call."""
    repo = "o/r"

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, path):
        return copy.deepcopy(self.routes.get(path))

    def get_all(self, path, key=None, pages=30):
        got = self.get(path) or []
        return got.get(key, []) if isinstance(got, dict) else got

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        return None, {}

    def download(self, path):
        return self.routes.get(path)

    def writes(self, method, prefix=""):
        return [(p, b) for m, p, b in self.calls if m == method and p.startswith(prefix)]


def routes(**over):
    pins = fixture("pins.json")
    r = {
        "pulls/12": fixture("pr.json"),
        f"contents/.factory/config.toml?ref={BASE}": b64(CFG),
        "pulls/12/files": fixture("pr_files.json"),
        "issues/7": {"number": 7, "labels": [{"name": "tier:T2"}], "body": "Add the thing."},
        f"contents/{SPEC_PATH}?ref={HEAD}": b64(SPEC) | {"sha": "blob104"},
        f"compare/sc1...{HEAD}": fixture("compare_ahead.json"),
        "issues/12/events": fixture("issue_events.json"),
        "issues/12/comments": [],
        "actions/artifacts?name=factory-pin": {"artifacts": [
            {"id": i + 1, "expired": False, "workflow_run": {"id": p["run"]["id"]}} for i, p in enumerate(pins)]},
    }
    for i, p in enumerate(pins):
        r[f"actions/runs/{p['run']['id']}"] = {"id": p["run"]["id"], "event": p["run"]["event"], "path": p["run"]["path"]}
        r[f"actions/artifacts/{i + 1}/zip"] = pin_zip(p["pin"])
    return r | over


def t3_routes(**over):  # a declared T3: the only tier I7 checks (D23)
    return routes(**{"issues/7": {"number": 7, "labels": [{"name": "tier:T3"}], "body": "Add the thing."}} | over)


class Gates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def run_gates(self, api, action="synchronize", sender="the-owner", pr=None, label=None):
        ev = {"action": action, "pull_request": pr or fixture("pr.json"), "sender": {"login": sender}}
        if label:
            ev["label"] = {"name": label}
        (self.tmp / "event.json").write_text(json.dumps(ev), "utf-8")
        env = {"GITHUB_EVENT_PATH": str(self.tmp / "event.json"), "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "9100",
               "GITHUB_EVENT_NAME": "pull_request_target", "RUNNER_TEMP": str(self.tmp), "GITHUB_OUTPUT": str(self.tmp / "out"),
               "GITHUB_WORKFLOW_REF": "o/r/.github/workflows/factory.yml@refs/heads/main"}
        with contextlib.redirect_stdout(io.StringIO()):
            gates.main(env, api)
        return api

    def statuses(self, api):
        return {b["context"]: (b["state"], b["description"]) for _, b in api.writes("POST", "statuses/")}

    def comment(self, api):
        (path, body), = api.writes("POST", "issues/12/comments") or api.writes("PATCH", "issues/comments/")
        return body["body"]

    def test_ApprovedT2(self):
        api = self.run_gates(FakeApi(routes()))
        st = self.statuses(api)
        self.assertEqual(st["factory/integrity"], ("success", "ok (1 raising flags)"))
        self.assertEqual(st["factory/tier"][0], "success")
        self.assertTrue(st["factory/tier"][1].startswith("T2: computed T2: integrity I2: removed assertions"))
        self.assertEqual(api.writes("POST", f"statuses/{HEAD}")[0][1]["target_url"], "https://github.com/o/r/actions/runs/9100")
        self.assertEqual(api.writes("POST", "issues/12/labels"), [])  # tier:T2 already present
        body = self.comment(api)
        self.assertTrue(body.startswith(gates.MARKER + "\n### factory gates (report-only): T2"))
        self.assertIn("**Integrity:** ok (0 failing, 1 raising)", body)

    def test_DeclaredT2NeedsNoPin(self):  # D23: T2 is approved in the kickoff session; no label events or pins are read
        pr = fixture("pr.json") | {"labels": [{"name": "tier:T2"}]}
        r = routes(**{"pulls/12": pr})
        for k in [k for k in r if k.startswith(("actions/", "issues/12/events"))]:
            del r[k]
        self.assertEqual(self.statuses(self.run_gates(FakeApi(r)))["factory/integrity"], ("success", "ok (1 raising flags)"))
        r["issues/7"]["labels"] = [{"name": "tier:T3"}]
        self.assertEqual(self.statuses(self.run_gates(FakeApi(r)))["factory/integrity"], ("failure", "failing: I7"))

    def test_PinReadsEventHeadBlob(self):
        r = t3_routes(**{f"contents/{SPEC_PATH}?ref=evhead": b64(SPEC) | {"sha": "blobEV"}})
        pr = fixture("pr.json")
        pr["head"]["sha"] = "evhead"  # the event's head (what the approver saw); the current head has moved on
        api = self.run_gates(FakeApi(r), action="labeled", label="spec-approved", sender="the-approver", pr=pr)
        pin = json.loads((self.tmp / "factory-pin" / "pin.json").read_text("utf-8"))
        self.assertEqual(pin, {"pr": 12, "head": "evhead", "spec": SPEC_PATH, "blob": "blobEV", "by": "the-approver",
                               "label_event_id": 104})
        self.assertEqual((self.tmp / "out").read_text("utf-8"),
                         f"pin=true\npin_path={self.tmp / 'factory-pin' / 'pin.json'}\n")
        st = self.statuses(api)  # the fresh pin is the latest for event 104, and head's blob differs from it
        self.assertEqual(st["factory/integrity"], ("failure", "failing: I7"))
        self.assertIn("spec changed after approval", self.comment(api))
        self.assertIn("Spec approval pinned: docs/specs/7-add-thing.md (blob blobEV)", self.comment(api))

    def test_PinAtCurrentHeadPasses(self):
        api = self.run_gates(FakeApi(t3_routes()), action="labeled", label="spec-approved", sender="the-approver")
        self.assertEqual(self.statuses(api)["factory/integrity"][0], "success")

    def test_PinNeedsClosingIssue(self):  # a plain doc under specs_dir (no id) must never be pinned as "the spec"
        pr = fixture("pr.json") | {"body": "## Intent\nNo closing keyword."}
        r = routes(**{f"contents/{SPEC_PATH}?ref={pr['head']['sha']}": b64("# readme\n") | {"sha": "blobR"}})
        self.run_gates(FakeApi(r), action="labeled", label="spec-approved", sender="the-approver", pr=pr)
        self.assertFalse((self.tmp / "factory-pin").exists())

    def test_LastClosingKeywordWins(self):  # fx ship appends `Closes #n` after the agent-written packet
        self.assertEqual(gates.issue_of({"body": "Intent: fixes #3 typo in the parent.\n\nCloses #7\n"}), 7)
        self.assertIsNone(gates.issue_of({"body": None}))

    def test_NonApproverPinRefused(self):
        api = self.run_gates(FakeApi(routes()), action="labeled", label="spec-approved", sender="the-owner")
        self.assertFalse((self.tmp / "factory-pin").exists())
        self.assertFalse((self.tmp / "out").exists())
        self.assertIn("spec-approved by the-owner ignored. Next: the-approver applies the label.", self.comment(api))

    def test_PinNeedsBaseConfig(self):
        r = routes(**{f"contents/.factory/config.toml?ref={HEAD}": b64(CFG)})
        del r[f"contents/.factory/config.toml?ref={BASE}"]
        api = self.run_gates(FakeApi(r), action="labeled", label="spec-approved", sender="the-approver")
        self.assertFalse((self.tmp / "factory-pin").exists())  # a PR can't name its own approver
        body = self.comment(api)
        self.assertIn("ignored", body)
        self.assertIn("config read from the PR head", body)

    def test_UntrustedTextEscaped(self):
        files = fixture("pr_files.json") + [{"filename": "infra/<!-- factory:gates -->@evil-->x.tf", "status": "added",
                                             "additions": 1, "deletions": 0, "changes": 1, "patch": "@@\n+x\n"}]
        comments = [{"id": 70, "user": {"login": "the-owner"}, "body": gates.MARKER + " forged by a person"},
                    {"id": 77, "user": {"login": "github-actions[bot]"}, "body": gates.MARKER + "\nold"}]
        api = self.run_gates(FakeApi(routes(**{"pulls/12/files": files, "issues/12/comments": comments})))
        (path, body), = api.writes("PATCH", "issues/comments/")
        self.assertEqual(path, "issues/comments/77")  # only our own bot comment is upserted
        text = body["body"]
        self.assertEqual((text.count("<!--"), text.count("-->")), (1, 1))  # just the real marker
        self.assertIn("&lt;!-- factory:gates --&gt;@​evil--&gt;x.tf", text)
        self.assertEqual(api.writes("POST", "issues/12/comments"), [])

    def test_DuplicateCommentRemoved(self):  # opened + labeled runs race and both POST; later runs keep one, drop the rest
        comments = [{"id": 77, "user": {"login": "github-actions[bot]"}, "body": gates.MARKER + "\nold"},
                    {"id": 78, "user": {"login": "github-actions[bot]"}, "body": gates.MARKER + "\nstale twin"}]
        api = self.run_gates(FakeApi(routes(**{"issues/12/comments": comments})))
        self.assertEqual([p for p, _ in api.writes("PATCH", "issues/comments/")], ["issues/comments/77"])
        self.assertEqual(api.writes("DELETE", "issues/comments/"), [("issues/comments/78", None)])

    def test_TierLabelSwapped(self):
        pr = fixture("pr.json")
        pr["labels"] = [{"name": "tier:T1"}, {"name": "spec-approved"}]
        api = self.run_gates(FakeApi(routes(**{"pulls/12": pr})))
        self.assertEqual(api.writes("DELETE"), [("issues/12/labels/tier%3AT1", None)])
        self.assertEqual(api.writes("POST", "issues/12/labels"), [("issues/12/labels", {"labels": ["tier:T2"]})])

    def test_IssueBlockSpecT1(self):
        block = SPEC.replace("tier = 2", "tier = 1")
        files = [{"filename": "src/app.py", "status": "modified", "additions": 3, "deletions": 1, "changes": 4,
                  "patch": "@@\n-x = 1\n+x = 2\n"}]
        pr = fixture("pr.json") | {"labels": []}
        r = routes(**{"pulls/12": pr, "pulls/12/files": files,
                      "issues/7": {"labels": [{"name": "tier:T1"}], "body": f"<!-- factory:spec -->\n{block}<!-- /factory:spec -->"}})
        api = self.run_gates(FakeApi(r))
        st = self.statuses(api)
        self.assertEqual(st["factory/integrity"], ("success", "ok (0 raising flags)"))
        self.assertEqual(st["factory/tier"][1], "T1: computed T1: 1 files, 4 code lines")

    def test_NoSpecFails(self):
        pr = fixture("pr.json") | {"body": "No issue link.", "labels": []}
        files = [{"filename": "src/app.py", "status": "modified", "additions": 3, "deletions": 1, "changes": 4,
                  "patch": "@@\n+y\n"}]
        api = self.run_gates(FakeApi(routes(**{"pulls/12": pr, "pulls/12/files": files})))
        self.assertEqual(self.statuses(api)["factory/integrity"], ("failure", "failing: I6"))
        self.assertIn("needs a spec. Next: /factory:shape <issue>", self.comment(api))

    def test_OnboardingUsesHeadConfig(self):
        r = routes(**{f"contents/.factory/config.toml?ref={HEAD}": b64(CFG)})
        del r[f"contents/.factory/config.toml?ref={BASE}"]
        self.assertIn("config read from the PR head", self.comment(self.run_gates(FakeApi(r))))


class WorkflowFiles(unittest.TestCase):
    def test_CallerTemplateIsSafe(self):
        text = (CORE / "plugins" / "factory" / "templates" / "factory.yml").read_text("utf-8")
        on = text.split("on:", 1)[1].split("permissions:", 1)[0]
        self.assertEqual([ln.strip() for ln in on.splitlines() if ln.strip().endswith(":")], ["pull_request_target:"])
        self.assertIn("\npermissions: {}\n", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("github.event.action == 'labeled' && github.run_id", text)  # a push can't drop the pin run
        self.assertNotIn("checkout", text)  # never check out PR code
        refs = re.findall(r"^\s*(?:- )?uses: (\S+)", text, re.M)
        self.assertEqual(len(refs), 2)
        for ref in refs:
            self.assertRegex(ref, r"@([0-9a-f]{40}|__FACTORY_CORE_SHA__)$")

    def test_AgentTemplateIsSafe(self):  # templates/factory-agent.yml: runs Claude with write tokens, so keep it tight
        text = (CORE / "plugins" / "factory" / "templates" / "factory-agent.yml").read_text("utf-8")
        on = text.split("\non:", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual([ln.strip() for ln in on.splitlines() if ln.strip().endswith(":")], ["issues:", "issue_comment:"])
        self.assertIn("\npermissions: {}\n", text)
        self.assertIn("if: github.event.label.name == 'factory' && github.event.sender.login == github.repository_owner",
                      text)  # only the owner's own label starts a run (G6)
        self.assertIn("github.event.sender.login == github.repository_owner && !github.event.issue.pull_request", text)
        self.assertEqual(text.count("contains(github.event."), 2)  # the interview: the owner's own `@factory` only
        self.assertEqual(text.count("id-token: write"), 2)
        self.assertNotIn("\nconcurrency:", text)  # per job: a skipped run (the bot's own comment) can't cancel a queued one
        self.assertEqual(text.count("    concurrency: {group: 'factory-"), 2)
        self.assertIn("--allowedTools Bash,Read,Write,Glob,Grep,Skill ", text)  # the interview writes issue bodies to a file
        self.assertNotRegex(text, r"run:.*\$\{\{ *github\.event")  # no event text in a shell line
        self.assertNotIn("secrets.GITHUB_TOKEN", text)
        for ref in re.findall(r"^\s*(?:- )?uses: (\S+)", text, re.M):
            self.assertRegex(ref, r"@[0-9a-f]{40}$")
        self.assertIn('echo "FACTORY_NEXT=$RUNNER_TEMP/factory-next-prompt" >> "$GITHUB_ENV"', text)  # no `claude --bg`
        for ph in ("shape", "build"):  # as fx launch sets it: else the shape session's Stop hook verifies as a build
            self.assertRegex(text, rf"- name: {ph} session\n(?:\s+if: .*\n)?\s+uses: .*\n\s+env: {{FACTORY_PHASE: {ph}}}")

    def test_MergerTemplateIsSafe(self):  # templates/factory-merger.yml: holds the merge token (D24)
        text = (CORE / "plugins" / "factory" / "templates" / "factory-merger.yml").read_text("utf-8")
        on = text.split("\non:", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual([ln.strip() for ln in on.splitlines() if ln.strip().endswith(":")], ["workflow_run:"])
        self.assertIn("\npermissions: {}\n", text)
        self.assertIn("permissions: {contents: read, pull-requests: read, issues: read, checks: read, statuses: read, "
                      "actions: read}", text)  # GITHUB_TOKEN only reads; the App token merges
        self.assertNotIn("checkout", text)
        self.assertNotRegex(text, r"(?m)^\s*(?:- )?run:")  # no shell in the workflow: only the pinned actions run
        self.assertEqual(text.count("${{ steps.app.outputs.token }}"), 1)
        refs = re.findall(r"^\s*(?:- )?uses: (\S+)", text, re.M)
        self.assertEqual(len(refs), 2)
        for ref in refs:
            self.assertRegex(ref, r"@([0-9a-f]{40}|__FACTORY_CORE_SHA__)$")
        action = (CORE / "ci" / "merge" / "action.yml").read_text("utf-8")
        self.assertIn('run: python3 "$GITHUB_ACTION_PATH/../merger.py"', action)

    def test_ActionRunsGates(self):
        text = (CORE / "ci" / "action.yml").read_text("utf-8")
        self.assertIn('run: python3 "$GITHUB_ACTION_PATH/gates.py"', text)
        self.assertIn("using: composite", text)


class Handler(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):
        Handler.seen.append((self.path, self.headers.get("Authorization")))
        if self.path.startswith("/repos/o/r/actions/artifacts/1/zip"):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_address[1]}/blob?sig=x")
            self.end_headers()
        elif self.path.startswith("/blob"):
            self.reply(200, b"ZIPBYTES")
        elif self.path.startswith("/repos/o/r/pulls/1/files"):
            page = int(self.path.rsplit("page=", 1)[1])
            self.reply(200, json.dumps([{"filename": f"f{page}-{i}"} for i in range(100 if page == 1 else 5)]).encode())
        elif self.path.startswith("/repos/o/r/boom"):
            self.reply(500, b"server error")
        else:
            self.reply(404, b"{}")

    def reply(self, code, data):
        self.send_response(code)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):  # noqa: A002 (base signature)
        pass


class RealApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.api = gates.Api("o/r", "SECRET", root=f"http://127.0.0.1:{cls.srv.server_address[1]}")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_DownloadDropsAuthorization(self):
        Handler.seen.clear()
        self.assertEqual(self.api.download("actions/artifacts/1/zip"), b"ZIPBYTES")
        self.assertEqual(Handler.seen, [("/repos/o/r/actions/artifacts/1/zip", "Bearer SECRET"), ("/blob?sig=x", None)])

    def test_PaginationAnd404(self):
        self.assertEqual(len(self.api.get_all("pulls/1/files")), 105)
        self.assertIsNone(self.api.get("nothing/here"))

    def test_ErrorEndsJob(self):
        with self.assertRaises(SystemExit) as cm:
            self.api.get("boom")
        self.assertIn("GitHub API GET boom -> 500", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
