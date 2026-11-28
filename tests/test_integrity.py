import unittest

from tests.helpers import fixture, fl
import integrity  # noqa: E402

CFG = fl.load_cfg_text('project = "x"\nquarantine = [{test = "tests/test_flaky.py::test_net", reason = "net"}]\n'
                       '[authority]\napprover = "the-approver"\n')
SPEC_PATH = "docs/specs/7-add-thing.md"


def spec(**meta):
    m = {"id": 7, "tier": 2, "scope": ["src/**", "tests/**"], "locked": ["tests/acceptance/**"], "spec_commit": "sc1"} | meta
    return {"meta": m, "sections": {"Acceptance checks": "- AC1: works → `pytest tests/acceptance/test_7_a.py`"},
            "path": SPEC_PATH, "issue": 7}


def case(files=None, **events):
    pr = fixture("pr.json") | {"files": fixture("pr_files.json") if files is None else files}
    ev = {"tier": None, "declared": 3, "compare": fixture("compare_ahead.json"), "label_events": fixture("issue_events.json"),
          "pins": fixture("pins.json"), "spec_blob": "blob104", "issue": 7} | events
    return pr, ev


def codes(flags, kind=None):
    return [fl_.code for fl_ in flags if kind is None or fl_.kind == kind]


class Integrity(unittest.TestCase):
    def flags(self, sp: object = "default", files=None, **events):
        pr, ev = case(files, **events)
        return integrity.check(pr, spec() if sp == "default" else sp, CFG, ev)

    def i7(self, flags):
        return next((fl_.msg for fl_ in flags if fl_.code == "I7"), "")

    def test_AcceptanceLockFromBaseConfig(self):  # audit A3: the lock doesn't depend on the agent-editable spec
        lock = {"glob": "tests/acceptance/test_7_*", "first": "c1" * 20,
                "compare": {"status": "ahead", "files": [{"filename": "tests/acceptance/test_7_a.py", "status": "modified"}]}}
        flags = self.flags(spec(locked=[], spec_commit=None), files=[], tier=1, declared=1, acc_lock=lock)
        self.assertEqual([(f.code, f.kind) for f in flags], [("I1", "raise:T2")])
        self.assertIn("acceptance tests changed after the commit that added them (c1c1c1c1c1c1): tests/acceptance/test_7_a.py", flags[0].msg)
        clean = lock | {"compare": {"status": "ahead", "files": [{"filename": "src/app.py", "status": "modified"}]}}
        self.assertEqual(self.flags(None, files=[], tier=0, declared=0, acc_lock=clean), [])  # no spec needed: CI's own lock
        self.assertEqual(codes(self.flags(None, files=[], tier=0, declared=0, acc_lock=lock | {"compare": None})), ["I1"])

    def test_Baseline(self):
        flags = self.flags(tier=2)
        self.assertEqual([(fl_.code, fl_.kind) for fl_ in flags], [("I2", "raise:T2")])  # fixture deletes one assert
        self.assertIn("removed assertions: tests/test_app.py: assert y == 2", flags[0].msg)
        self.assertNotIn("assertEqual", flags[0].msg)  # moved line cancels out

    def test_LockedChangedFails(self):
        cmp = fixture("compare_ahead.json")
        cmp["files"].append({"filename": "tests/acceptance/test_7_a.py", "status": "modified", "changes": 1})
        flags = self.flags(compare=cmp)
        self.assertEqual(codes(flags, "fail"), ["I1"])
        self.assertIn("tests/acceptance/test_7_a.py (tests/acceptance/**)", flags[-1].msg)
        cmp["files"][-1] = {"filename": "tests/other.py", "previous_filename": "tests/acceptance/test_7_a.py"}
        self.assertEqual(codes(self.flags(compare=cmp), "fail"), ["I1"])  # renamed away still counts

    def test_SpecFileAfterSpecCommitNotI1(self):
        self.assertEqual(codes(self.flags(spec(locked=["tests/acceptance/**", SPEC_PATH]))), ["I2"])

    def test_RebaseDropsSpecCommitFails(self):
        for cmp in ({"status": "diverged", "files": []}, None):
            flags = self.flags(compare=cmp)
            self.assertEqual(codes(flags, "fail"), ["I1"])
            self.assertIn("spec_commit not in PR history. Next:", flags[-1].msg)
        self.assertEqual(codes(self.flags(spec(spec_commit="")), "fail"), ["I1"])  # locks without spec_commit

    def test_AssertRemovedRaisesT2(self):
        patches = {"tests/a.spec.ts": "@@\n-  expect(x).toBe(1)\n+  // gone\n",
                   "tests/b_test.py": "@@\n+@pytest.mark.skip(reason='later')\n def test_b():\n",
                   "tests/c.test.ts": "@@\n+it.only('c', () => {})\n+// eslint-disable-next-line\n",
                   "src/x.py": "@@\n-assert ok\n"}  # not a tests file
        files = [{"filename": n, "status": "modified", "changes": 2, "patch": p} for n, p in patches.items()]
        msgs = [fl_.msg for fl_ in self.flags(files=files) if fl_.code == "I2"]
        self.assertEqual(len(msgs), 3)
        self.assertIn("removed assertions: tests/a.spec.ts: expect(x).toBe(1)", msgs[0])
        self.assertIn("added skip/only/todo: tests/b_test.py", msgs[1])
        self.assertIn("tests/c.test.ts: it.only", msgs[1])
        self.assertIn("added suppressions: tests/c.test.ts: // eslint-disable-next-line", msgs[2])

    def test_NoPatchRaisesT2(self):
        files = [{"filename": "tests/big_test.py", "status": "modified", "changes": 9000},
                 {"filename": "tests/moved_test.py", "status": "renamed", "changes": 0, "previous_filename": "tests/m.py"}]
        flags = self.flags(files=files)
        self.assertEqual([fl_.msg for fl_ in flags], ["patch unavailable: tests/big_test.py"])
        pr, ev = case([])
        flags = integrity.check(pr | {"files_truncated": True}, spec(), CFG, ev)
        self.assertEqual([(fl_.code, fl_.kind) for fl_ in flags], [("I2", "raise:T2")])

    def test_TestMovedOutStillInspected(self):  # moving a test out of the test globs doesn't hide removed asserts
        files = [{"filename": "legacy/pay_check.py", "previous_filename": "tests/test_pay.py", "status": "renamed",
                  "changes": 3, "patch": "@@\n-    assert paid\n+    pass\n"}]
        msgs = [fl_.msg for fl_ in self.flags(files=files) if fl_.code == "I2"]
        self.assertEqual(msgs, ["removed assertions: legacy/pay_check.py: assert paid"])

    def test_CompareCutoffRaises(self):  # the compare API lists at most 300 files
        cmp = fixture("compare_ahead.json")
        cmp["files"] = [{"filename": f"src/f{i}.py", "status": "modified", "changes": 1} for i in range(300)]
        self.assertIn(("I1", "raise:T2"), [(fl_.code, fl_.kind) for fl_ in self.flags(compare=cmp)])
        self.assertNotIn("I1", codes(self.flags()))

    def test_OutOfScopeRaisesT2(self):
        files = [{"filename": n, "status": "modified", "changes": 1, "patch": "@@\n+x\n"}
                 for n in ("src/a.py", "infra/x.tf", SPEC_PATH)]
        flags = self.flags(files=files)
        self.assertEqual([(fl_.code, fl_.msg) for fl_ in flags], [("I4", "files outside the spec scope: infra/x.tf")])
        self.assertEqual(self.flags(spec(scope=[]), files=files), [])  # no scope: skipped
        self.assertEqual(self.flags(spec(tier=0), files=files), [])  # an explicit T0 spec: exempt
        self.assertEqual(codes(self.flags(spec(tier=None), files=files)), ["I4"])  # an issue-block spec has no tier

    def test_ThresholdAndQuarantine(self):
        files = [{"filename": "web/vitest.config.ts", "status": "modified", "changes": 1, "patch": "@@\n+x\n"}]
        self.assertEqual(codes(self.flags(spec(scope=[]), files=files)), ["I3"])
        sp = spec(scope=[])
        sp["sections"]["Acceptance checks"] += "\n- AC2: net → `pytest tests/test_flaky.py::test_net`"
        self.assertEqual(codes(self.flags(sp, files=[]), "fail"), ["I5"])

    def test_NoSpecFails(self):
        files = [{"filename": "src/a.py", "status": "modified", "changes": 1, "patch": "@@\n+x\n"}]
        self.assertEqual(self.flags(None, files=files, tier=None), [])  # first pass: tier unknown
        self.assertEqual(self.flags(None, files=files, tier=0), [])
        flags = self.flags(None, files=files, tier=1)
        self.assertEqual([(fl_.code, fl_.kind, fl_.msg) for fl_ in flags],
                         [("I6", "fail", "needs a spec. Next: /factory:shape 7")])

    def test_StacksAreData(self):  # D23: one row per stack; an overlay's patterns are added, never replace the core's
        go = fl.load_cfg_text('tests = ["tests/**", "**/*_test.go"]\n'  # Go test files are test files here
                              "[integrity]\nassert = ['\\bt\\.(?:Error|Fatal)f?\\(']\nskip = ['\\bt\\.Skip\\(']\n")
        rows = [  # (cfg, test file, removed line, added line, expected I2 message start or None)
            (CFG, "tests/test_a.py", "assert x == 1", "", "removed assertions"),
            (CFG, "web/a.test.ts", "expect(x).toBe(1)", "", "removed assertions"),
            (CFG, "web/a.test.ts", "", "it.only('x')", "added skip/only/todo"),
            (CFG, "tests/test_a.py", "", "y = f()  # type: ignore", "added suppressions"),
            (CFG, "pkg/a_test.go", 'if err != nil { t.Fatalf("x") }', "", None),  # Go is not a core stack
            (go, "pkg/a_test.go", 'if err != nil { t.Fatalf("x") }', "", "removed assertions"),
            (go, "pkg/a_test.go", "", 't.Skip("later")', "added skip/only/todo"),
            (go, "tests/test_a.py", "assert x == 1", "", "removed assertions"),  # union: the core list still applies
        ]
        for cfg, name, rem, add, want in rows:
            with self.subTest(name=name, rem=rem, add=add):
                patch = "@@\n" + (f"-{rem}\n" if rem else "") + (f"+{add}\n" if add else "")
                f = {"filename": name, "status": "modified", "additions": 1, "deletions": 1, "changes": 2, "patch": patch}
                msgs = [x.msg for x in integrity.check({"files": [f], "number": 1}, None, cfg, {"tier": None}) if x.code == "I2"]
                self.assertEqual([m.split(":")[0] for m in msgs], [want] if want else [])

    def test_ApprovedT3Passes(self):
        self.assertEqual("", self.i7(self.flags(tier=3)))
        self.assertEqual("", self.i7(self.flags(tier=1, declared=1, label_events=[], pins=[])))  # T1 needs no approval

    def test_OnlyDeclaredT3NeedsApproval(self):  # D23: T2 is approved in the session; a computed raise never asks
        for declared, computed in ((2, 2), (2, 3), (0, 3), (None, 3)):
            with self.subTest(declared=declared, computed=computed):
                self.assertEqual("", self.i7(self.flags(tier=computed, declared=declared, label_events=[], pins=[])))
        self.assertIn("no spec-approved label event", self.i7(self.flags(tier=3, declared=3, label_events=[], pins=[])))

    def test_UnapprovedT3Fails(self):
        pr, ev = case(tier=2, label_events=[], pins=[])
        pr["labels"] = [{"name": "tier:T3"}]
        msg = self.i7(integrity.check(pr, spec(), CFG, ev))
        self.assertIn("no spec-approved label. Next: as the-approver, review the spec and add spec-approved", msg)
        self.assertIn("set authority.approver", self.i7(integrity.check(
            pr, spec(), fl.load_cfg_text('project = "x"\n'), ev)))
        self.assertIn("needs a spec file", self.i7(self.flags(spec() | {"path": None}, tier=3)))  # issue-body spec

    def test_NonApproverLabelRefused(self):
        evs = fixture("issue_events.json") + [{"id": 105, "event": "labeled", "actor": {"login": "the-owner"},
                                               "label": {"name": "spec-approved"}, "performed_via_github_app": None}]
        self.assertIn("spec-approved by the-owner ignored", self.i7(self.flags(tier=2, label_events=evs)))
        evs[-1] = evs[-1] | {"actor": {"login": "the-approver"}, "performed_via_github_app": {"slug": "some-app"}}
        self.assertIn("ignored (needs the-approver, not via an app)", self.i7(self.flags(tier=2, label_events=evs)))

    def test_PinFromPullRequestRunIgnored(self):
        pins = fixture("pins.json")
        pins[1]["run"]["event"] = "pull_request"
        self.assertIn("no factory-pin artifact for the latest", self.i7(self.flags(tier=2, pins=pins)))
        pins[1]["run"] = {"event": "pull_request_target", "path": ".github/workflows/evil.yml"}
        self.assertIn("no factory-pin artifact", self.i7(self.flags(tier=2, pins=pins)))
        pins[1]["run"]["path"] = integrity.PIN_PATH
        pins[1]["pin"]["pr"] = 13  # a pin from another PR
        self.assertIn("no factory-pin artifact", self.i7(self.flags(tier=2, pins=pins)))

    def test_ForgedMarkerInCommentIgnored(self):
        pr, ev = case(tier=2, pins=[], comments=fixture("comments.json"))  # comments are never read
        self.assertIn("no factory-pin artifact", self.i7(integrity.check(pr, spec(), CFG, ev)))

    def test_LatestPinWins(self):
        self.assertEqual("", self.i7(self.flags(tier=2, spec_blob="blob104")))
        self.assertIn("spec changed after approval", self.i7(self.flags(tier=2, spec_blob="blobold")))  # old pin ignored
        only_old = fixture("pins.json")[:1]
        self.assertIn("no factory-pin artifact", self.i7(self.flags(tier=2, pins=only_old, spec_blob="blobold")))

    def test_SpecChangedAfterPinFails(self):
        msg = self.i7(self.flags(tier=3, spec_blob="blobnew"))
        self.assertEqual(msg, "spec changed after approval. Next: the-approver re-applies spec-approved on "
                              "https://github.com/o/r/pull/12")


if __name__ == "__main__":
    unittest.main()
