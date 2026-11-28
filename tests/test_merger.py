import contextlib, io, json, tempfile, unittest, zipfile
from pathlib import Path

from tests.helpers import fixture
from tests.test_gates import BASE, HEAD, SPEC, FakeApi, b64
import merger  # noqa: E402

GREEN_STATUSES = [{"context": "factory/integrity", "state": "success"}, {"context": "factory/tier", "state": "success"}]
DOC = {"filename": "docs/guide.md", "status": "modified", "additions": 2, "deletions": 0, "changes": 2,
       "patch": "@@ -1 +1,3 @@\n x\n+a\n+b\n"}
CODE = {"filename": "src/app.py", "status": "modified", "additions": 2, "deletions": 0, "changes": 2,
        "patch": "@@ -1,1 +1,3 @@\n def f(x):\n+    if x:\n+        return 1\n"}
T1_ISSUE = {"labels": [{"name": "tier:T1"}],
            "body": "<!-- factory:spec -->\n" + SPEC.replace("tier = 2", "tier = 1") + "<!-- /factory:spec -->"}
T1 = {"issues/7": T1_ISSUE}


class MergeApi:
    """The merge token's API: records calls; GitHub answers a merge with {"merged": true}."""
    def __init__(self, merged=True):
        self.merged, self.calls = merged, []

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        return (b'{"merged": true}' if self.merged else None), {}

    def writes(self, method):
        return [(p, b) for m, p, b in self.calls if m == method]


def lcov_zip(text, name="lcov.info"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def routes(files, cfg="", pr=None, report=None, checks=None, statuses=None, behind=0, extra=None):
    p = fixture("pr.json") | {"state": "open", "labels": [], "body": "Docs.", "user": {"login": "claude[bot]"}} | (pr or {})
    return {
        "pulls/12": p,
        f"contents/.factory/config.toml?ref={BASE}": b64(cfg),
        "pulls/12/files": files,
        f"compare/main...{HEAD}": {"behind_by": behind},
        f"compare/sc1...{HEAD}": fixture("compare_ahead.json"),
        f"commits/{HEAD}/check-runs": {"check_runs": [{"name": "test", "status": "completed", "conclusion": "success"}]
                                       if checks is None else checks},
        f"commits/{HEAD}/status": {"statuses": GREEN_STATUSES if statuses is None else statuses},
        f"actions/runs?head_sha={HEAD}&event=pull_request&status=success": {"workflow_runs": [{"id": 5}]},
        "actions/runs/5/artifacts": {"artifacts": [{"id": 50, "name": "coverage"}] if report is not None else []},
        "actions/artifacts/50/zip": lcov_zip(report or ""),
        f"commits/{HEAD}/pulls": [p],
    } | (extra or {})


class Merger(unittest.TestCase):
    def run_merger(self, r, merge=None):
        api, merge = FakeApi(r), merge or MergeApi()
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()) as out:
            (Path(d) / "event.json").write_text(json.dumps({"workflow_run": {"head_sha": HEAD}}), "utf-8")
            merger.main({"GITHUB_EVENT_PATH": str(Path(d) / "event.json"), "GITHUB_REPOSITORY": "o/r"}, api, merge)
        self.assertEqual(api.calls, [])  # the read token writes nothing
        return merge.writes("PUT"), out.getvalue()

    def test_T0Merges(self):  # Phase 2 red-team (e): a genuine T0 PR merges with no human action
        merged, out = self.run_merger(routes([DOC]))
        self.assertEqual(merged, [("pulls/12/merge", {"merge_method": "squash", "sha": HEAD})])
        self.assertIn("#12 merging: T0 <= auto_merge_ceiling T0: computed T0: docs only, 2 lines; changed-line coverage 0/0", out)

    def test_T1NeedsCeilingAndCoverage(self):  # G1 before any auto-merge; red-team (h) end to end
        body = {"body": "Closes #7"}
        merged, out = self.run_merger(routes([CODE], pr=body, report="SF:src/app.py\nDA:2,1\nDA:3,1\n", extra=T1))
        self.assertEqual(merged, [])  # the default ceiling is T0
        self.assertIn("T1 > auto_merge_ceiling T0", out)
        cfg = "[authority]\nauto_merge_ceiling = 1\n[mutation]\nscore = 72\n"
        merged, out = self.run_merger(routes([CODE], cfg, pr=body, report="SF:src/app.py\nDA:2,1\nDA:3,1\n", extra=T1))
        self.assertEqual(len(merged), 1, out)
        merged, out = self.run_merger(routes([CODE], cfg, pr=body, report="SF:src/app.py\nDA:2,1\nDA:3,0\n", extra=T1))
        self.assertEqual(merged, [])
        self.assertIn("T2 > auto_merge_ceiling T1: computed T2: changed-line coverage 1/2: below 80%", out)
        merged, out = self.run_merger(routes([CODE], cfg, pr=body, extra=T1))
        self.assertIn("no changed-line coverage: no `coverage` artifact holding lcov.info", out)

    def test_RecomputesWithItsOwnCopy(self):  # red-team (d): a green factory/tier from the PR's run counts for nothing
        gate = {"filename": ".github/workflows/factory.yml", "status": "modified", "additions": 1, "deletions": 1,
                "changes": 2, "patch": "@@ -1 +1 @@\n-a\n+b\n"}
        merged, out = self.run_merger(routes([DOC, gate], "[authority]\nauto_merge_ceiling = 1\n[mutation]\nscore = 72\n"))
        self.assertEqual(merged, [])
        self.assertIn("T3 > auto_merge_ceiling T1: computed T3: gate file .github/workflows/factory.yml", out)

    def test_MutationScoreCapsTheCeiling(self):  # D26: >= 60 T1, 40-59 T0, < 40 none, no score T0
        for score, want in (("", "T1 > auto_merge_ceiling T0 (no mutation score recorded)"),
                            ("score = 45\n", "T1 > auto_merge_ceiling T0 (capped by the mutation score)"),
                            ("score = 59.9\n", "T1 > auto_merge_ceiling T0 (capped by the mutation score)"),
                            ("score = 60\n", "merging: T1 <= auto_merge_ceiling T1")):
            with self.subTest(score=score):
                cfg = f"[authority]\nauto_merge_ceiling = 1\n[mutation]\n{score}"
                _, out = self.run_merger(routes([CODE], cfg, pr={"body": "Closes #7"}, extra=T1,
                                                report="SF:src/app.py\nDA:2,1\nDA:3,1\n"))
                self.assertIn(want, out)
        merged, out = self.run_merger(routes([DOC], "[mutation]\nscore = 39\n"))
        self.assertEqual(merged, [])
        self.assertIn("T0 > no auto-merge (mutation score below 40%)", out)
        self.assertIn("merging: T0 <= auto_merge_ceiling T0", self.run_merger(routes([DOC], "[mutation]\nscore = 40\n"))[1])
        _, out = self.run_merger(routes([CODE], "[mutation]\nscore = 90\n", pr={"body": "Closes #7"}, extra=T1,
                                        report="SF:src/app.py\nDA:2,1\nDA:3,1\n"))
        self.assertIn("T1 > auto_merge_ceiling T0: computed T1", out)  # a score never raises the ceiling
        for bad in ("mutation = 60\n", "[mutation]\nscore = true\n"):  # malformed: T0, never a crash
            with self.subTest(bad=bad):
                self.assertIn("T0 <= auto_merge_ceiling T0", self.run_merger(routes([DOC], bad))[1])

    def test_HeadConfigIgnored(self):  # main's ceiling, never the PR's: no base config means the core default T0
        r = routes([CODE], pr={"body": "Closes #7"}, report="SF:src/app.py\nDA:2,1\nDA:3,1\n",
               extra=T1 | {f"contents/.factory/config.toml?ref={HEAD}": b64("[authority]\nauto_merge_ceiling = 1\n[mutation]\nscore = 72\n")})
        del r[f"contents/.factory/config.toml?ref={BASE}"]
        merged, out = self.run_merger(r)
        self.assertEqual(merged, [])
        self.assertIn("T1 > auto_merge_ceiling T0", out)

    def test_HeadScoreIgnored(self):  # base: ceiling 1, score 30; the PR's own config claims 72
        r = routes([CODE], "[authority]\nauto_merge_ceiling = 1\n[mutation]\nscore = 30\n", pr={"body": "Closes #7"},
                   report="SF:src/app.py\nDA:2,1\nDA:3,1\n",
                   extra=T1 | {f"contents/.factory/config.toml?ref={HEAD}": b64("[authority]\nauto_merge_ceiling = 1\n"
                                                                              "[mutation]\nscore = 72\n")})
        merged, out = self.run_merger(r)
        self.assertEqual(merged, [])
        self.assertIn("T1 > no auto-merge (mutation score below 40%)", out)

    def test_EveryConditionHolds(self):
        rows = [  # (routes overrides, expected reason)
            ({"pr": {"user": {"login": "someone"}}}, "author someone is not an agent identity (o, claude[bot]"),
            ({"pr": {"labels": [{"name": "hold"}]}}, "the `hold` label is on"),
            ({"pr": {"draft": True}}, "not an open, ready PR"),
            ({"behind": 2}, "behind main: update the branch"),
            ({"checks": [{"name": "test", "status": "in_progress", "conclusion": None}]}, "checks not green: test"),
            ({"checks": [{"name": "lint", "status": "completed", "conclusion": "failure"}]}, "checks not green: lint"),
            ({"statuses": GREEN_STATUSES[:1]}, "checks not green: factory/tier (missing)"),
            ({"statuses": [GREEN_STATUSES[0], {"context": "factory/tier", "state": "pending"}]},
             "checks not green: factory/tier"),
        ]
        for over, want in rows:
            with self.subTest(want=want):
                merged, out = self.run_merger(routes([DOC], **over))
                self.assertEqual(merged, [])
                self.assertIn(want, out)
        self.assertEqual(len(self.run_merger(routes([DOC], "[authority]\nagents = [\"someone\"]\n",
                                                    pr={"user": {"login": "someone"}}))[0]), 1)

    def test_WokenByTheGatesRun(self):  # a pull_request_target run: found through the run's pull_requests
        r = routes([DOC], extra={f"commits/{HEAD}/pulls": []})
        api, merge = FakeApi(r), MergeApi()
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            ev = {"workflow_run": {"head_sha": BASE, "pull_requests": [{"number": 12}]}}
            (Path(d) / "event.json").write_text(json.dumps(ev), "utf-8")
            merger.main({"GITHUB_EVENT_PATH": str(Path(d) / "event.json"), "GITHUB_REPOSITORY": "o/r"}, api, merge)
        self.assertEqual(merge.writes("PUT"), [("pulls/12/merge", {"merge_method": "squash", "sha": HEAD})])

    def test_RefusedMergeFailsTheJob(self):  # GitHub's 405/409 end a call; a 404 returns nothing: both are failures
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as e:
            self.run_merger(routes([DOC]), MergeApi(merged=False))
        self.assertIn("#12 merge refused or not found", err.getvalue())
        self.assertEqual(str(e.exception), "factory merger: failed for #12")

    def test_NoOpenPr(self):
        self.assertEqual(self.run_merger(routes([DOC], extra={f"commits/{HEAD}/pulls": []})), ([], ""))

    def test_HugeReportRefused(self):
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("lcov.info", "x")
        api = FakeApi(routes([CODE], report="") | {"actions/artifacts/50/zip": z.getvalue()})
        real, merger.MAX_REPORT = merger.MAX_REPORT, 0
        self.addCleanup(setattr, merger, "MAX_REPORT", real)
        cfg = merger.gates.fl.load_cfg_text("")
        self.assertEqual(merger.coverage(api, cfg, {"head": {"sha": HEAD}}), "lcov.info in `coverage` is over 0 MB")
