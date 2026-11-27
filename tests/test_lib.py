import os, subprocess, sys, tempfile, time, unittest

from tests.helpers import FactoryTest, fl, write

SPEC = '+++\nid = 7\ntier = 1\nspec_commit = "abc123"\nlocked = ["tests/acceptance/**"]\n+++\n' \
       "## Goal\nDo the thing.\n\n## Acceptance checks\n- AC1 works\n"


class Phase(FactoryTest):
    def ctx(self, **kw):
        return fl.repo_ctx(self.repo(**kw))

    def test_PhaseFromState(self):
        ctx = self.ctx()
        write(ctx["work"] / "spec.md", SPEC)  # spec_commit alone would infer build; state.json wins
        fl.save_state(ctx, phase="shape")
        self.assertEqual(fl.phase(fl.repo_ctx(ctx["root"])), "shape")
        fl.save_state(ctx, phase="build")
        self.assertEqual(fl.phase(fl.repo_ctx(ctx["root"])), "build")

    def test_PhaseT0Build(self):
        ctx = self.ctx()  # T0: no spec at all, only intent.md and state.json written by fx build
        write(ctx["work"] / "intent.md", "Fix the typo in README.\n")
        fl.save_state(ctx, tier=0, phase="build", spec_path=".factory/work/7/intent.md")
        self.assertEqual(fl.phase(fl.repo_ctx(ctx["root"])), "build")

    def test_PhaseResumeWithoutEnv(self):
        ctx = self.ctx()
        self.assertIsNone(fl.phase(ctx))  # no work dir, no env -> hooks no-op
        write(ctx["work"] / "spec.md", SPEC.replace('"abc123"', '""'))
        self.assertEqual(fl.phase(fl.repo_ctx(ctx["root"])), "shape")  # spec without spec_commit
        write(ctx["work"] / "spec.md", SPEC)
        self.assertEqual(fl.phase(fl.repo_ctx(ctx["root"])), "build")  # plain `claude --resume`
        os.environ["FACTORY_PHASE"] = "shape"
        self.assertEqual(fl.phase(fl.repo_ctx(ctx["root"])), "shape")  # env overrides

    def test_NonFactoryBranchHasNoPhase(self):
        ctx = self.ctx(branch="main-work")
        self.assertIsNone(ctx["issue"])
        self.assertIsNone(fl.phase(ctx))


class RepoCtx(FactoryTest):
    def test_ParsesRepoIssueAndConfig(self):
        root = self.repo(branch="feat/34-x-y", cfg='project = "p"\nbranch = "{type}/{issue}-{slug}"\n'
                                                 '[verify]\nfull_locks = ["docker"]\n')
        ctx = fl.repo_ctx(root)
        self.assertEqual((ctx["repo"], ctx["issue"], ctx["branch"]), ("o/r", "34", "feat/34-x-y"))
        self.assertEqual(ctx["work"], root / ".factory" / "work" / "34")
        self.assertEqual(ctx["cfg"]["verify"]["full_locks"], ["docker"])
        self.assertEqual(ctx["cfg"]["verify"]["cap_s"], 600)  # default kept

    def test_NoConfig(self):
        ctx = fl.repo_ctx(self.repo(cfg=None))
        self.assertIsNone(ctx["cfg"])
        self.assertIsNone(ctx["issue"])

    def test_OutsideGit(self):
        self.assertIsNone(fl.repo_ctx(self.tmp))


class Config(unittest.TestCase):
    def test_MergeDeep(self):
        base = {"a": {"b": 1, "c": [1]}, "d": 2}
        out = fl.merge(base, {"a": {"c": [9]}, "e": 3})
        self.assertEqual(out, {"a": {"b": 1, "c": [9]}, "d": 2, "e": 3})
        self.assertEqual(base["a"]["c"], [1])  # base not mutated

    def test_OverlayKeepsDefaults(self):
        cfg = fl.load_cfg_text('project = "x"\n[packet.headings]\nintent = "## What changes"\n')
        self.assertEqual(cfg["packet"]["headings"]["intent"], "## What changes")
        self.assertEqual(cfg["packet"]["headings"]["rollback"], "## Rollback")
        self.assertEqual(cfg["tiers"]["docs_globs"], ["docs/**", "**/*.md"])

    def test_ParseIssue(self):
        self.assertEqual(fl.parse_issue("f/{issue}-{slug}", "f/12-add-x"), "12")
        self.assertEqual(fl.parse_issue("{type}/{issue}-{slug}", "fix/3-a"), "3")
        self.assertIsNone(fl.parse_issue("f/{issue}-{slug}", "main"))
        self.assertIsNone(fl.parse_issue("f/{issue}-{slug}", None))


class Spec(unittest.TestCase):
    def test_FrontmatterAndSections(self):
        meta, body, secs = fl.load_spec(text=SPEC)
        self.assertEqual((meta["id"], meta["tier"], meta["spec_commit"]), (7, 1, "abc123"))
        self.assertEqual(secs, {"Goal": "Do the thing.", "Acceptance checks": "- AC1 works"})
        self.assertTrue(body.startswith("## Goal"))

    def test_IssueBodyBlock(self):
        issue = f"Intro text\n\n<!-- factory:spec -->\n{SPEC}<!-- /factory:spec -->\nfooter"
        self.assertEqual(fl.extract_block(issue).splitlines()[0], "+++")
        self.assertEqual(fl.load_spec(text=issue)[0]["id"], 7)

    def test_NoFrontmatter(self):
        meta, _, secs = fl.load_spec(text="## Goal\nx\n")
        self.assertEqual((meta, secs), ({}, {"Goal": "x"}))

    def test_FromFile(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(fl.load_spec(write(os.path.join(d, "s.md"), SPEC))[0]["tier"], 1)


GLOB_TABLE = [  # (pattern, path, expected)
    ("**/*.md", "README.md", True), ("**/*.md", "docs/a/b.md", True), ("*.md", "docs/a.md", False),
    ("docs/**", "docs/a/b.md", True), ("docs/**", "docsx/a.md", False),
    ("src/**/x.ts", "src/x.ts", True), ("src/**/x.ts", "src/a/b/x.ts", True), ("src/*/x.ts", "src/a/b/x.ts", False),
    ("test?.py", "test1.py", True), ("test?.py", "test/.py", False),
    ("[ab].txt", "a.txt", True), ("[!ab].txt", "a.txt", False), ("[!ab].txt", "c.txt", True),
    ("**/CODEOWNERS", ".github/CODEOWNERS", True), ("**/CODEOWNERS", "CODEOWNERS", True),
    ("CLAUDE.md", "api/CLAUDE.md", False), ("**/CLAUDE.md", "api/CLAUDE.md", True),
    ("tests/acceptance/test_12_*", "tests/acceptance/test_12_login.py", True),
    ("tests/acceptance/test_12_*", "tests/acceptance/test_123_login.py", False),
    ("test/acceptance/{issue}-*.spec.ts", "test/acceptance/{issue}-a.spec.ts", True),
    ("**/.eslintrc*", ".eslintrc.json", True), ("**/tsconfig*.json", "web/tsconfig.eslint.json", True),
    ("**/*.spec.*", "src/a/b.spec.ts", True), ("**/*.test.*", "src/a.tsx", False),
    ("a.b", "axb", False), ("a+b", "a+b", True), ("**", "any/deep/path", True),
    ("src/**", "src\\win\\path.py", True), (".factory/work/**", ".factory/work/7/state.json", True),
]


class Glob(unittest.TestCase):
    def test_GlobTable(self):
        for pat, path, want in GLOB_TABLE:
            with self.subTest(pat=pat, path=path):
                self.assertIs(fl.glob_match(pat, path), want)

    def test_GateFiles(self):
        cfg = fl.load_cfg_text('project = "x"\n[tiers]\ngate_extra = ["AGENTS.md"]\n')
        self.assertEqual(fl.is_gate(".factory/config.toml", cfg), ".factory/**")
        self.assertIsNone(fl.is_gate(".factory/work/7/progress.md", cfg))
        self.assertEqual(fl.is_gate("AGENTS.md", cfg), "AGENTS.md")
        self.assertIsNone(fl.is_gate("src/a.py", cfg))


HOLDER = """import sys, time
sys.path.insert(0, sys.argv[1])
import factory_lib as fl
with fl.verify_locks(sys.argv[2:], 2, 5) as got:
    print("held" if got else "failed", flush=True)
    sys.stdin.read()
"""


class Locks(FactoryTest):
    def hold_in_child(self, *resources):
        p = subprocess.Popen([sys.executable, "-c", HOLDER, str(fl.Path(fl.__file__).parent), *resources],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.assertEqual(p.stdout.readline().strip(), "held")
        return p

    def release(self, p):
        p.stdin.close()
        p.wait(10)
        p.stdout.close()

    def test_LockTwoSlots(self):
        with fl.verify_locks([], 2, 1) as a, fl.verify_locks([], 2, 1) as b:
            self.assertTrue(a and b)
            t = time.monotonic()
            with fl.verify_locks([], 2, 0.3) as c:
                self.assertFalse(c)
            self.assertGreaterEqual(time.monotonic() - t, 0.3)
        with fl.verify_locks([], 2, 0.3) as d:
            self.assertTrue(d)  # released on exit
        child = self.hold_in_child()  # a separate process (as hooks are) holds one slot
        try:
            with fl.verify_locks([], 2, 0.3) as e, fl.verify_locks([], 2, 0.3) as f:
                self.assertEqual((e, f), (True, False))
        finally:
            self.release(child)

    def test_FullLockExclusive(self):
        self.user_config("lock_wait_s = 0.4\n")
        ctx = fl.repo_ctx(self.repo())
        child = self.hold_in_child("full")
        try:
            res = fl.run_verify(ctx, "full")
            self.assertEqual((res["ok"], res["reason"], res["failed"]), (False, "lock-timeout", ["lock"]))
            self.assertTrue(fl.run_verify(ctx, "fast")["ok"])  # fast doesn't take `full`
        finally:
            self.release(child)
        self.assertTrue(fl.run_verify(ctx, "full")["ok"])


TREE = """import subprocess, sys, time
subprocess.Popen([sys.executable, "-c",
    "import time,sys\\nwhile True:\\n open(sys.argv[1],'a').write('x'); time.sleep(0.1)", sys.argv[1]])
time.sleep(60)
"""


class RunTree(FactoryTest):
    def test_TimeoutKillsTree(self):
        beat = self.tmp / "beat"
        t = time.monotonic()
        _, _, timed_out = fl.run_tree([sys.executable, "-c", TREE, str(beat)], self.tmp, 2)
        self.assertTrue(timed_out)
        self.assertLess(time.monotonic() - t, 15)
        self.assertTrue(beat.exists(), "grandchild never started")
        time.sleep(0.5)  # let a pending write land
        size = beat.stat().st_size
        time.sleep(0.8)
        self.assertEqual(beat.stat().st_size, size, "grandchild still running after the tree kill")

    def test_ExitCodeAndOutput(self):
        rc, out, timed_out = fl.run_tree([sys.executable, "-c", "print('hi'); raise SystemExit(3)"], self.tmp, 30)
        self.assertEqual((rc, out.strip(), timed_out), (3, "hi", False))


class RunVerify(FactoryTest):
    def test_PassWritesLastVerify(self):
        ctx = fl.repo_ctx(self.repo())
        res = fl.run_verify(ctx, "fast")
        self.assertEqual((res["ok"], res["reason"], res["failed"]), (True, "ok", []))
        self.assertEqual(res["lines"], ["factory-verify: lint PASS 1s", "factory-verify: unit PASS 2s"])
        lv = fl.load_json(ctx["work"] / "state.json")["last_verify"]
        self.assertEqual((lv["mode"], lv["ok"], lv["fingerprint"]), ("fast", True, fl.fingerprint(ctx["root"])))

    def test_FailNamesChecks(self):
        ctx = fl.repo_ctx(self.repo(verify='echo "factory-verify: lint PASS 1s"\necho boom\n'
                                           'echo "factory-verify: unit FAIL 2s"\nexit 1\n'))
        res = fl.run_verify(ctx, "fast")
        self.assertEqual((res["ok"], res["reason"], res["failed"]), (False, "fail", ["unit"]))
        self.assertIn("boom", res["tail"])

    def test_NonZeroWithoutLines(self):
        res = fl.run_verify(fl.repo_ctx(self.repo(verify="exit 4\n")), "fast")
        self.assertEqual((res["ok"], res["failed"]), (False, ["verify"]))

    def test_NoScript(self):
        res = fl.run_verify(fl.repo_ctx(self.repo(verify=None)), "fast")
        self.assertEqual((res["ok"], res["reason"]), (False, "no-verify-script"))

    def test_Timeout(self):
        ctx = fl.repo_ctx(self.repo(cfg='project = "d"\n[verify]\ncap_s = 1\n', verify="sleep 30\n"))
        res = fl.run_verify(ctx, "fast")
        self.assertEqual((res["ok"], res["reason"]), (False, "timeout"))
        self.assertLess(res["seconds"], 15, "sh's grandchild (MSYS fork) survived the tree kill")

    def test_OverBudgetLogged(self):
        ctx = fl.repo_ctx(self.repo(cfg='project = "d"\n[verify]\nfast_budget_s = 0\n', verify="sleep 0.3\n"))
        res = fl.run_verify(ctx, "fast")
        self.assertEqual((res["ok"], res["over_budget"]), (True, True))
        rec = fl.load_json(self.home / "logs" / "hooks.jsonl")
        self.assertEqual((rec["hook"], rec["decision"], rec["issue"], rec["repo"]), ("verify", "over_budget", "7", "o/r"))


def packet(**over):
    h = dict(fl.DEFAULTS["packet"]["headings"])
    body = {k: "x" for k in h} | {"not_verified": "Browser flow not run."} | over
    return "\n".join(f"{h[k]}\n{body[k]}" for k in h) + "\n"


class Packet(unittest.TestCase):
    cfg = fl.load_cfg_text('project = "x"\n')

    def test_Complete(self):
        self.assertEqual(fl.packet_check(packet(), self.cfg, 1), [])

    def test_MissingHeading(self):
        text = packet().replace("## Rollback\n", "")
        self.assertEqual(fl.packet_check(text, self.cfg, 0), ["missing heading '## Rollback'"])

    def test_TooLong(self):
        self.assertIn("lines > max 40", fl.packet_check(packet() + "\n" * 30, self.cfg, 0)[0])

    def test_NotVerifiedEmptyT1(self):
        text = packet(not_verified="")
        self.assertEqual(fl.packet_check(text, self.cfg, 0), [])
        self.assertIn("'Not verified' is empty", fl.packet_check(text, self.cfg, 1)[0])

    def test_MappedNotVerifiedNeedsLine(self):
        cfg = fl.load_cfg_text('project = "x"\n[packet]\nextra = ["### Checklist"]\n'
                               '[packet.headings]\nnot_verified = "## Follow-ups"\n')
        h = cfg["packet"]["headings"]
        base = "\n".join(f"{v}\nx" for k, v in h.items() if k != "not_verified") + "\n### Checklist\n- [x] a\n"
        self.assertIn("'Not verified' is empty", fl.packet_check(base + "## Follow-ups\nlater stuff\n", cfg, 1)[0])
        self.assertEqual(fl.packet_check(base + "## Follow-ups\nNot verified: prod deploy.\n", cfg, 1), [])
        self.assertEqual(fl.packet_check(base.replace("### Checklist", "") + "## Follow-ups\nNot verified: a\n", cfg, 1),
                         ["missing heading '### Checklist'"])

    def test_ReviewLineT2(self):
        self.assertIn("Review:", fl.packet_check(packet(), self.cfg, 2)[0])
        self.assertEqual(fl.packet_check(packet(needs_you="Review: /code-review medium, 2 findings"), self.cfg, 2), [])
        self.assertEqual(fl.packet_check(packet(needs_you="Review: fresh-context subagent, 0 findings"), self.cfg, 2), [])

    def test_OverlayKeyStillRequired(self):  # D23: keys dropped from the defaults stay required where an overlay sets them
        cfg = fl.load_cfg_text('project = "x"\n[packet.headings]\ndeviations = "## Deviations from spec"\n')
        self.assertEqual(fl.packet_check(packet(), cfg, 1), ["missing heading '## Deviations from spec'"])


if __name__ == "__main__":
    unittest.main()
