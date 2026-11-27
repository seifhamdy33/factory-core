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
