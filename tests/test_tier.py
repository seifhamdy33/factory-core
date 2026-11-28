import unittest

from tests.helpers import fixture, fl
import integrity  # noqa: E402
import tier  # noqa: E402

CFG = fl.load_cfg_text('project = "x"\n[tiers]\nsensitive = ["src/auth/**"]\ngate_extra = ["AGENTS.md"]\n')


def f(name, add=1, dele=0, prev=None):
    out = {"filename": name, "status": "modified", "additions": add, "deletions": dele, "changes": add + dele}
    return out | ({"previous_filename": prev, "status": "renamed"} if prev else {})


class Tier(unittest.TestCase):
    def test_GateFileIsT3(self):
        for path in (".github/workflows/ci.yml", ".claude/settings.json", "CLAUDE.md", "web/CLAUDE.md",
                     ".factory/verify", "AGENTS.md", "src/auth/login.ts"):
            with self.subTest(path=path):
                t, reasons = tier.compute([f("src/a.py"), f(path)], CFG)
                self.assertEqual(t, 3)
                self.assertIn(path, reasons[0])
        t, _ = tier.compute([f("tools/x.yml", prev=".github/workflows/x.yml")], CFG)  # moved away from a gate path
        self.assertEqual(t, 3)
        self.assertEqual(tier.compute([f(".factory/work/7/progress.md")], CFG)[0], 0)  # work dir is not a gate (and .md)

    def test_DocsOnlyIsT0(self):
        t, reasons = tier.compute([f("docs/guide.md", 20, 5), f("README.md", 3)], CFG)
        self.assertEqual(t, 0)
        self.assertEqual(reasons[0], "computed T0: docs only, 28 lines")
        self.assertEqual(reasons[-1], tier.NOT_EVALUATED)
        self.assertEqual(tier.compute([f("docs/guide.md", 900)], CFG)[0], 0)  # size never raises a tier (D23)
        self.assertEqual(tier.compute([f("docs/guide.md"), f("src/a.py")], CFG)[0], 1)

    def test_LockfileRaisesT2(self):
        for path in ("package-lock.json", "web/pnpm-lock.yaml", "requirements-dev.txt", "pyproject.toml", "api/package.json"):
            with self.subTest(path=path):
                t, reasons = tier.compute([f(path)], CFG)
                self.assertEqual(t, 2)
                self.assertIn("dependency manifest", reasons[0])

    def test_WeakOverlayWeakensNothing(self):  # Phase 2 red-team (i), G5
        weak = fl.load_cfg_text('tests = []\n[tiers]\ndocs_globs = ["**"]\n[integrity]\nthreshold_globs = []\n')
        self.assertEqual(tier.compute([f("src/a.py")], weak)[0], 1)  # code is not docs, whatever docs_globs says
        self.assertEqual(tier.compute([f("docs/guide.md")], weak)[0], 0)
        narrow = fl.load_cfg_text('[tiers]\ndocs_globs = ["docs/**"]\n')
        self.assertEqual(tier.compute([f("README.md")], narrow)[0], 1)  # narrowing works
        rm = {"filename": "tests/test_a.py", "status": "modified", "additions": 0, "deletions": 1, "changes": 1,
              "patch": "@@\n-assert x == 1\n"}
        flags = integrity.check({"files": [rm], "number": 1}, None, weak, {"tier": None})
        self.assertIn("I2", [x.code for x in flags])
        self.assertEqual(tier.compute([rm], weak, flags)[0], 2)  # a removed assertion still raises to T2
        th = integrity.check({"files": [f("jest.config.js")], "number": 1}, None, weak, {"tier": None})
        self.assertTrue(any("jest.config.js" in x.msg for x in th))  # threshold files are still flagged

    def test_ParseLcov(self):
        rep = tier.parse_lcov("TN:\nSF:/home/runner/work/r/r/src/a.py\nDA:1,1\nDA:2,0\nDA:3,2,abc\nend_of_record\n"
                              "SF:src\\b.py\nDA:x,1\nDA:4,junk\nend_of_record\n")
        self.assertEqual(rep, {"/home/runner/work/r/r/src/a.py": {1: 1, 2: 0, 3: 2}, "src/b.py": {4: 0}})
        self.assertEqual(tier.parse_lcov(None), {})

    def test_AddedLines(self):
        patch = "@@ -1,3 +1,5 @@\n ctx\n-old\n+new\n+\n ctx2\n+tail\n\\ No newline at end of file\n@@ -40 +42,2 @@\n+x\n y\n"
        self.assertEqual(tier.added_lines(patch), [2, 5, 42])  # blank added lines don't count

    def test_ChangedLineCoverage(self):  # Phase 2 red-team (h), G1
        branch = f("src/a.py", 4) | {"patch": "@@ -1,2 +1,6 @@\n def f(x):\n+    if x:\n+        return 1\n"
                                              "+    return 0\n     pass\n"}
        test = f("tests/test_a.py", 2) | {"patch": "@@ -0,0 +1,2 @@\n+def test_f():\n+    assert f(0) == 0\n"}
        untested = {"/w/src/a.py": {1: 1, 2: 1, 3: 0, 4: 1}}
        t, reasons = tier.compute([branch], CFG, coverage=untested)
        self.assertEqual(t, 2)
        self.assertEqual(reasons[0], "computed T2: changed-line coverage 2/3: below 80%")
        covered = {"/w/src/a.py": {1: 1, 2: 1, 3: 1, 4: 1}}
        t, reasons = tier.compute([branch, test], CFG, coverage=covered)
        self.assertEqual(t, 1)
        self.assertEqual(reasons[-1], "changed-line coverage 3/3")
        self.assertEqual(tier.compute([branch], CFG, coverage="no coverage artifact for abc123")[1][0],
                         "computed T2: no changed-line coverage: no coverage artifact for abc123")
        new = f("src/new.py", 2) | {"patch": "@@ -0,0 +1,2 @@\n+def g():\n+    return 2\n"}
        self.assertEqual(tier.compute([new], CFG, coverage=covered)[0], 2)  # not in the report, but .py is: uncovered
        conf = f("deploy/app.yml", 2) | {"patch": "@@ -0,0 +1 @@\n+a: 1\n"}
        self.assertEqual(tier.compute([conf], CFG, coverage=covered), (1, ["computed T1: 1 files, 2 code lines",
                                                                           "changed-line coverage 0/0"]))
        self.assertEqual(tier.compute([f("docs/a.md"), f("tests/test_b.py")], CFG, coverage=covered)[0], 1)  # no code
        self.assertEqual(tier.compute([branch], CFG, coverage={})[1][0],  # an empty or garbled report passes nothing
                         "computed T2: no changed-line coverage: the coverage report has no line data")
        self.assertEqual(tier.compute([f("src/a.py", 900)], CFG, coverage=covered)[0], 2)  # no patch: unknown
        self.assertEqual(tier.compute([f("data/big.json", 900), branch], CFG, coverage=covered)[0], 1)  # not measured
        util = f("util.py", 2) | {"patch": "@@ -0,0 +1,2 @@\n+def u():\n+    return 1\n"}
        deep = {"/w/r/packages/a/util.py": {1: 1, 2: 1}, "/w/r/src/a.py": {1: 1, 2: 1, 3: 1, 4: 1}}
        self.assertEqual(tier.compute([util], CFG, coverage=deep)[1][0],  # a same-named file deeper down never counts
                         "computed T2: changed-line coverage 0/2: below 80%")
        self.assertEqual(tier.compute([util, branch], CFG, coverage=deep | {"/w/r/util.py": {1: 1, 2: 1}})[0], 1)
        self.assertEqual(tier.report_root([branch], {"src/a.py": {}}), "")  # relative paths
        self.assertEqual(tier.compute([branch], CFG)[1][-1], tier.NOT_EVALUATED)  # the gates: not evaluated

    def test_OverlayDepsAreAdded(self):
        cfg = fl.load_cfg_text('[tiers]\ndeps = ["**/Package.swift"]\n')
        self.assertEqual(tier.compute([f("app/Package.swift")], cfg)[0], 2)
        self.assertEqual(tier.compute([f("go.mod")], cfg)[0], 2)  # the core list still applies

    def test_SizeIsInformationOnly(self):  # D23: a big change stays at its risk tier
        t, reasons = tier.compute([f("src/a.py", 250, 50), f("docs/big.md", 500)], CFG)  # docs lines don't count
        self.assertEqual((t, reasons[0]), (1, "computed T1: 2 files, 300 code lines"))
        self.assertEqual(tier.compute([f(f"src/{i}.py") for i in range(40)], CFG)[0], 1)

    def test_IntegrityRaisesAndDeclared(self):
        flags = [integrity.Flag("I4", "raise:T2", "files outside the spec scope: src/b.py"),
                 integrity.Flag("I1", "fail", "x")]
        t, reasons = tier.compute([f("src/b.py")], CFG, flags)
        self.assertEqual(t, 2)
        self.assertIn("integrity I4", reasons[0])
        t, reasons = tier.compute([f("src/b.py")], CFG, [], declared="T3")
        self.assertEqual(t, 3)
        self.assertEqual(reasons[:2], ["declared T3 (issue label or spec) > computed T1", "computed T1: 1 files, 1 code lines"])
        self.assertEqual(tier.compute([f(".github/x.yml")], CFG, [], declared=1)[0], 3)  # max(), never lowered

    def test_TierNum(self):
        self.assertEqual([tier.tier_num(v) for v in ("T2", "t1", 3, "0", None, "high", "T4", -1)],
                         [2, 1, 3, 0, None, None, None, None])  # out of range: not a tier

    def test_FixtureFiles(self):
        t, reasons = tier.compute(fixture("pr_files.json"), CFG)
        self.assertEqual(t, 1)
        self.assertEqual(reasons[0], "computed T1: 3 files, 21 code lines")


if __name__ == "__main__":
    unittest.main()
