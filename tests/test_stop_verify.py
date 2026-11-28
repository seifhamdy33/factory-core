import json, os, unittest
from unittest import mock

from tests.helpers import FactoryTest, fl, git, load_hook, run_script, write

sv = load_hook("stop_verify")
RED = '#!/bin/sh\necho "factory-verify: lint PASS 1s"\necho "AssertionError: expected 2"\n' \
      'echo "factory-verify: unit FAIL 3s"\nexit 1\n'
NOTES = ".factory/work/7/progress.md"
COUNTING_RED = 'echo x >> "$FACTORY_TEST_COUNT"\necho "factory-verify: unit FAIL 1s"\nexit 1\n'


class StopVerify(FactoryTest):
    def stop(self, ctx, active=False):
        return sv.handle(f'{{"cwd": "{ctx["root"].as_posix()}", "session_id": "s1", "stop_hook_active": '
                         f'{"true" if active else "false"}}}')

    def stop_state(self, ctx):
        return fl.load_json(ctx["work"] / "state.json")["stop"]

    def set_verify(self, ctx, text):
        write(ctx["root"] / ".factory" / "verify", text)

    def test_RedBlocksGreenAllows(self):
        ctx = self.build_ctx(verify=RED)
        code, msg = self.stop(ctx)
        self.assertEqual(code, 2)
        self.assertIn("Failing: unit", msg)
        self.assertIn("AssertionError: expected 2", msg)
        self.assertTrue(msg.endswith(sv.NEXT_FIX.format(notes=NOTES)))
        self.set_verify(ctx, "#!/bin/sh\necho 'factory-verify: unit PASS 1s'\n")
        self.assertEqual(self.stop(ctx), (0, ""))
        self.assertNotIn("count", self.stop_state(ctx))
        self.assertEqual([(r["decision"], r["reason"]) for r in self.log_lines()], [("block", "fail"), ("allow", "green")])
        self.assertEqual(self.log_lines()[0]["session_id"], "s1")

    def test_ThreeStrikesThenBlocker(self):
        ctx = self.build_ctx(verify=RED)
        results = [self.stop(ctx) for _ in range(3)]
        self.assertEqual([c for c, _ in results], [2, 2, 2])
        self.assertNotIn("Stop iterating", results[1][1])
        self.assertTrue(results[2][1].startswith(sv.BLOCKER_ASK.format(notes=NOTES)))
        st = self.stop_state(ctx)
        self.assertEqual((st["count"], st["blocker_requested"]), (3, True))
        self.set_verify(ctx, '#!/bin/sh\necho "factory-verify: lint FAIL 1s"\nexit 1\n')  # a new failure resets
        self.assertEqual(self.stop(ctx)[0], 2)
        self.assertEqual(self.stop_state(ctx)["count"], 1)

    def test_BlockerBeforeStrikesAllowsOnce(self):
        ctx = self.build_ctx(verify=RED)
        self.assertEqual(self.stop(ctx)[0], 2)  # one strike only
        write(ctx["work"] / "progress.md", "## Notes\nx\n\n## Blocker\nShould AC2 use UTC?\n")
        self.assertEqual(self.stop(ctx), (0, ""))
        self.assertEqual(self.log_lines()[-1]["reason"], "blocker")

    def test_UserNotesAndBlockedHeading(self):  # D23: the user's own handoff file and its `## Blocked` heading
        self.user_config('[session]\nnotes = ".claude-work/TASKS.md"\n')
        ctx = self.build_ctx(verify=RED)
        code, msg = self.stop(ctx)
        self.assertEqual(code, 2)
        self.assertTrue(msg.endswith("add `## Blocker` to .claude-work/TASKS.md if you're stuck."))
        write(ctx["work"] / "progress.md", "## Blocker\nignored: not the notes file\n")
        self.assertEqual(self.stop(ctx)[0], 2)
        write(ctx["root"] / ".claude-work" / "TASKS.md", "# TASKS\n## Blocked\n- need the staging URL\n## Next\nx\n")
        self.assertEqual(self.stop(ctx), (0, ""))
        self.assertEqual(sv.blocker_hash(ctx["root"] / ".claude-work" / "TASKS.md"),
                         sv.blocker_hash(write(self.tmp / "n.md", "## Blocked\n- need the staging URL\n")))
        self.assertIsNone(sv.blocker_hash(write(self.tmp / "m.md", "## Blockers-ish\n## Blockade\nx\n")))

    def test_BlockerConsumedOnce(self):
        ctx = self.build_ctx(verify=RED)
        write(ctx["work"] / "progress.md", "## Blocker\nNeed the staging URL.\n")
        self.assertEqual(self.stop(ctx)[0], 0)
        self.assertEqual(self.stop(ctx)[0], 2)  # same Blocker text: no second free stop (e.g. after --resume)
        write(ctx["work"] / "progress.md", "## Blocker\nNeed the staging URL.\nAlso: which region?\n")
        self.assertEqual(self.stop(ctx)[0], 0)
        self.set_verify(ctx, "#!/bin/sh\necho 'factory-verify: unit PASS 1s'\n")
        self.assertEqual(self.stop(ctx)[0], 0)  # green keeps the consumed hash
        self.set_verify(ctx, RED)
        self.assertEqual(self.stop(ctx)[0], 2)

    def test_StopHookActiveReuse(self):
        count = self.tmp / "count"
        os.environ["FACTORY_TEST_COUNT"] = count.as_posix()
        self.addCleanup(os.environ.pop, "FACTORY_TEST_COUNT", None)
        ctx = self.build_ctx(verify=COUNTING_RED)
        runs = lambda: len(count.read_text().split())  # noqa: E731
        self.assertEqual(self.stop(ctx)[0], 2)
        self.assertEqual(self.stop(ctx, active=True)[0], 2)
        self.assertEqual(runs(), 1)  # unchanged tree: reused
        self.assertEqual(self.stop_state(ctx)["count"], 2)  # a reused red still counts
        self.assertTrue(self.log_lines()[-1]["reused"])
        write(ctx["root"] / "src" / "app.py", "x = 2\n")
        self.assertEqual(self.stop(ctx, active=True)[0], 2)
        self.assertEqual(runs(), 2)  # tree changed: re-run
        self.stop(ctx, active=False)
        self.assertEqual(runs(), 3)  # first Stop of a turn always runs

    def test_InternalErrorBlocks(self):
        ctx = self.build_ctx(verify=RED)
        with mock.patch.object(fl, "run_verify", side_effect=RuntimeError("boom")):
            results = [self.stop(ctx) for _ in range(3)]
        self.assertEqual([c for c, _ in results], [2, 2, 2])
        self.assertIn("factory hook error: RuntimeError: boom", results[0][1])
        self.assertTrue(results[2][1].startswith(sv.BLOCKER_ASK.format(notes=NOTES)))
        self.assertEqual(self.stop_state(ctx)["sig"], "hook-error")
        self.assertEqual(sv.handle("not json")[0], 0)  # unknown session, no FACTORY_PHASE: allow
        os.environ["FACTORY_PHASE"] = "build"
        self.assertEqual(sv.handle("not json")[0], 2)

    def test_ErrorBeforeCtxInBuildWorktreeBlocks(self):  # plain `claude --resume`, no FACTORY_PHASE, broken overlay
        ctx = self.build_ctx(verify=RED)
        write(ctx["root"] / ".factory" / "config.toml", "project = [unclosed\n")
        sub = ctx["root"] / "src"
        sub.mkdir(exist_ok=True)
        for cwd in (ctx["root"], sub):
            code, msg = sv.handle(f'{{"cwd": "{cwd.as_posix()}", "session_id": "s1"}}')
            self.assertEqual(code, 2)
            self.assertIn("factory hook error: TOMLDecodeError", msg)
        self.assertEqual(self.log_lines()[-1]["reason"], "hook-error")
        fl.save_state(ctx, phase="shape")  # a shape session stays a silent no-op
        self.assertEqual(sv.handle(f'{{"cwd": "{ctx["root"].as_posix()}"}}'), (0, ""))

    def test_TimeoutCountsStrikeNativePath(self):  # hooks get `C:\...` cwd on Windows; a verify timeout is a strike
        ctx = self.build_ctx(verify=RED)
        res = {"ok": False, "failed": ["unit"], "reason": "timeout", "tail": ""}
        raw = json.dumps({"cwd": str(ctx["root"]), "session_id": "s1"})
        with mock.patch.object(fl, "run_verify", return_value=res):
            results = [sv.handle(raw) for _ in range(3)]
        self.assertEqual([c for c, _ in results], [2, 2, 2])
        self.assertIn(sv.NEXT["timeout"], results[0][1])
        self.assertTrue(results[2][1].startswith(sv.BLOCKER_ASK.format(notes=NOTES)))

    def test_LockTimeoutCountsStrike(self):
        self.user_config("verify_slots = 1\nlock_wait_s = 0.3\n")
        ctx = self.build_ctx()
        with fl.verify_locks([], 1, 1) as held:
            self.assertTrue(held)
            code, msg = self.stop(ctx)
            self.assertEqual(self.stop(ctx, active=True)[0], 2)  # retried, not reused
        self.assertEqual(code, 2)
        self.assertIn("lock-timeout", msg)
        self.assertIn("Next: end your turn; verify will retry.", msg)
        self.assertEqual(self.stop_state(ctx)["count"], 2)
        self.assertEqual(self.stop(ctx, active=True), (0, ""))  # lock free: runs, green

    def test_NoVerifyScriptBlocks(self):
        ctx = self.build_ctx(verify=None)
        code, msg = self.stop(ctx)
        self.assertEqual(code, 2)
        self.assertIn("no-verify-script", msg)
        self.assertIn("templates/verify.sh", msg)

    def test_OverBudgetAllowsAndLogs(self):
        ctx = self.build_ctx(cfg='project = "d"\n[verify]\nfast_budget_s = 0\n',
                             verify="sleep 0.3\necho 'factory-verify: unit PASS 0s'\n")
        self.assertEqual(self.stop(ctx), (0, ""))
        logs = self.log_lines()
        self.assertEqual([(r["hook"], r["decision"]) for r in logs], [("verify", "over_budget"), ("stop_verify", "allow")])
        self.assertTrue(logs[1]["over_budget"])

    def test_InactiveInShape(self):
        ctx = self.build_ctx(verify=RED)
        fl.save_state(ctx, phase="shape")
        self.assertEqual(self.stop(ctx), (0, ""))
        self.assertEqual(self.log_lines(), [])

    def test_NotAFactoryRepoNoop(self):  # not on GitHub and no overlay; a github.com repo needs no overlay (D23)
        os.environ["FACTORY_PHASE"] = "build"
        root = self.repo(cfg=None, verify=RED)
        git(root, "remote", "set-url", "origin", "https://gitlab.example/o/r.git")
        self.assertEqual(sv.handle(f'{{"cwd": "{root.as_posix()}"}}'), (0, ""))
        self.assertEqual(self.log_lines(), [])
        self.assertFalse((root / ".factory" / "work").exists())
        git(root, "remote", "set-url", "origin", "git@github.com:o/r.git")
        self.assertEqual(sv.handle(f'{{"cwd": "{root.as_posix()}"}}')[0], 2)  # no overlay: the build's verify still runs

    def test_ScriptEndToEnd(self):
        ctx = self.build_ctx(verify=RED)
        code, out, err = run_script("stop_verify", {"cwd": str(ctx["root"]), "session_id": "s9"})
        self.assertEqual((code, out), (2, ""))
        self.assertIn("Failing: unit", err)
        code, out, err = run_script("stop_verify", {"cwd": str(self.tmp)})  # not a git repo
        self.assertEqual((code, out, err), (0, "", ""))


if __name__ == "__main__":
    unittest.main()
