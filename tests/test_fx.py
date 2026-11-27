import contextlib, importlib.machinery, importlib.util, io, json, os, shutil
from pathlib import Path
from unittest import mock

from tests.helpers import CORE, FactoryTest, fl, git, write

_loader = importlib.machinery.SourceFileLoader("fx", str(CORE / "bin" / "fx"))
_spec = importlib.util.spec_from_loader("fx", _loader)
assert _spec
fx = importlib.util.module_from_spec(_spec)
_loader.exec_module(fx)

PACKET = "\n".join(f"{h}\n{'Not verified: the prod path.' if 'Not verified' in h else 'x'}"
                   for h in fl.DEFAULTS["packet"]["headings"].values()) + "\n"


def call(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = fx.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class FakeRun:
    """Stands in for fx.run: answers gh calls from `gh`, records everything, runs real git unless faked."""

    def __init__(self, gh=None, fake_git=("push", "fetch")):
        self.gh, self.fake_git, self.calls = gh or {}, fake_git, []

    def __call__(self, cmd, cwd=None):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        if cmd[0] == "gh":
            key = next((k for k in self.gh if " ".join(cmd).startswith(k)), None)
            val = self.gh.get(key, "")
            return val if isinstance(val, str) else json.dumps(val)
        if cmd[0] == "git" and cmd[1] in self.fake_git or cmd[0] != "git":
            return ""
        return self.real(cmd, cwd)

    real = staticmethod(fx.run)

    def has(self, *prefix):
        return any(c[:len(prefix)] == list(prefix) for c in self.calls)


class Pure(FactoryTest):
    def test_Slug(self):
        self.assertEqual(fx.slug("Add the Thing: v2 (beta)!"), "add-the-thing-v2-beta")
        s = fx.slug("A very long issue title that keeps on going well past forty characters")
        self.assertLessEqual(len(s), 40)
        self.assertFalse(s.endswith("-"))
        self.assertEqual(fx.slug("إضافة ميزة 🚀"), "issue")  # no ASCII: still a parseable branch
        self.assertEqual(fl.parse_issue("f/{issue}-{slug}", "f/12-" + fx.slug("??")), "12")

    def test_BranchAndWorktreeNames(self):
        cfg = fl.load_cfg_text('project = "p"\nbranch = "{type}/{issue}-{slug}"\nbranch_types = ["feat", "fix"]\n'
                               'worktree_name = "api-{slug}"\n')
        self.assertEqual(fx.names(cfg, "7", "Fix login", [{"name": "type:fix"}]), ("fix/7-fix-login", "api-fix-login"))
        self.assertEqual(fx.names(cfg, "7", "Fix login", []), ("feat/7-fix-login", "api-fix-login"))
        dflt = fl.load_cfg_text('project = "p"\n')
        self.assertEqual(fx.names(dflt, "7", "Add thing", []), ("f/7-add-thing", "7-add-thing"))

    def test_LabelTier(self):
        self.assertEqual(fx.label_tier([{"name": "tier:T0"}, {"name": "bug"}]), 0)
        self.assertEqual(fx.label_tier([{"name": "tier:T1"}, {"name": "tier:T3"}]), 3)
        self.assertEqual(fx.label_tier([], default=2), 2)

    def test_RefRefusals(self):
        root = self.repo()
        with contextlib.chdir(root), mock.patch.object(fx.os, "getcwd", return_value=str(root)):  # chdir first: it saves the real cwd
            code, _, err = call("shape", "seven")
            self.assertIn("bad ref 'seven'\nNext: use <n>", err)
            code, _, err = call("shape", "api#7")
            self.assertIn("no [workspace] repos.api", err)
            self.assertIn("\nNext: ", err)
        with contextlib.chdir(self.tmp):
            code, _, err = call("build", "7")
        self.assertEqual(code, 1)
        self.assertIn("is not a factory repo", err)


def repo_entry(key, prs, wts=None, approver="the-approver"):
    cfg = fl.load_cfg_text(f'project = "p"\n[authority]\napprover = "{approver}"\n')
    return {"key": key, "cfg": cfg, "repo": f"o/{key}", "prs": prs, "wts": wts or {}}


def pr(n, head, state="OPEN", labels=(), repo="api"):  # the PR_FIELDS fx reads
    return {"number": n, "url": f"https://github.com/o/{repo}/pull/{n}", "state": state, "headRefName": head,
            "labels": [{"name": x} for x in labels]}


class Waiting(FactoryTest):
    def wt(self, issue, **state):
        path = self.tmp / f"wt{issue}"
        (path / ".factory" / "work" / issue).mkdir(parents=True, exist_ok=True)
        return {"path": path, "state": state}

    def test_MergeFirstWaitingItem(self):
        api = repo_entry("api", [pr(3, "f/3-api-part")],
                         {"3": self.wt("3", branch="f/3-api-part", parent="o/ws#1", pr="https://github.com/o/api/pull/3")})
        web = repo_entry("web", [pr(4, "f/4-web-part", repo="web"), pr(5, "f/5-solo", repo="web")],
                         {"4": self.wt("4", branch="f/4-web-part", parent="o/ws#1", pr="https://github.com/o/web/pull/4")})
        self.assertEqual(fx.waiting([api, web]), [
            "review/merge: https://github.com/o/api/pull/3",
            "merge api PR https://github.com/o/api/pull/3 first: https://github.com/o/web/pull/4",
            "review/merge: https://github.com/o/web/pull/5"])
        api["prs"][0]["state"] = "MERGED"  # once api merges, web is plain review/merge (and api needs a clean)
        self.assertEqual(fx.waiting([api, web])[:2], ["`fx clean api#3`", "review/merge: https://github.com/o/web/pull/4"])

    def test_ShipFailedWaitingItem(self):
        blocked = self.wt("9", branch="f/9-b")
        write(blocked["path"] / ".factory" / "work" / "9" / "progress.md", "# p\n\n## Blocker\nneeds a key\n")
        r = repo_entry(None, [pr(1, "f/1-a", labels=["tier:T3"]), pr(2, "f/2-b", labels=["tier:T3", "spec-approved"]),
                              pr(3, "f/3-c", labels=["tier:T2"]),  # T3 only: a T2 PR needs no approval label
                              pr(6, "feature/not-factory"), pr(7, "f/7-m", state="MERGED")],
                       {"8": self.wt("8", branch="f/8-a", ship="failed:unit,lint"), "9": blocked,
                        "7": self.wt("7", branch="f/7-m", pr="https://github.com/o/api/pull/7")})
        self.assertEqual(fx.waiting([r]), [
            "as the-approver, add `spec-approved`: https://github.com/o/api/pull/1",
            "review/merge: https://github.com/o/api/pull/2",
            "review/merge: https://github.com/o/api/pull/3",
            "`fx clean 7`",
            "8: ship failed at unit,lint. Next: `fx build 8`",
            f"unblock: {blocked['path'] / '.factory' / 'work' / '9' / 'progress.md'}"])

    def test_UserNotesBlockedIsWaiting(self):
        self.user_config('[session]\nnotes = ".claude-work/TASKS.md"\n')
        w = self.wt("9", branch="f/9-b")
        write(w["path"] / ".claude-work" / "TASKS.md", "# T\n## Blocked\n- which region?\n")
        self.assertEqual(fx.waiting([repo_entry(None, [], {"9": w})]), [f"unblock: {w['path'] / '.claude-work' / 'TASKS.md'}"])

    def test_EmptyWorkspace(self):
        self.assertEqual(fx.waiting([]), [])
        with contextlib.chdir(self.repo()):
            code, _, err = call("ship")  # on f/7-add-thing but no worktree: refuse, never a traceback
        self.assertEqual(code, 1)
        self.assertIn("Next: ", err)

    def test_TableShowsVerifyAndShip(self):
        r = repo_entry(None, [], {"7": self.wt("7", tier=2, phase="build", ship="ok", pr="u",
                                               last_verify={"ok": False, "failed": ["unit"], "mode": "fast", "seconds": 12.4})})
        self.assertEqual(fx.table([r]).splitlines()[1].split(), ["7", "T2", "build", "red", "unit", "fast", "12s", "u", "ok"])
        r["wts"]["7"]["state"]["last_verify"] = {"ok": True, "failed": [], "mode": "fast", "seconds": 420, "over_budget": True}
        self.assertIn("over budget", fx.table([r]))

    def test_DriftWarnsWhenInstalledCopyDiffers(self):  # SP1: a cache copy needs `claude plugin update`
        inst = self.tmp / "cache" / "factory"
        shutil.copytree(CORE / "plugins" / "factory", inst, ignore=shutil.ignore_patterns("__pycache__"))
        write(self.tmp / "cfg" / "plugins" / "installed_plugins.json",
              json.dumps({"plugins": {"factory@factory-core": [{"installPath": str(inst)}]}}))
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.tmp / "cfg")}):
            self.assertIsNone(fx.drift())
            write(inst / "hooks" / "reinject.py", "# stale\n")
            self.assertIn("Next: claude plugin update factory@factory-core", fx.drift())


class Flows(FactoryTest):
    def main_repo(self, **kw):
        main = self.repo(branch="main-work", **kw)  # the overlay lands on main, as after the overlay PR merges
        git(main, "checkout", "-q", "main")
        git(main, "merge", "-q", "main-work")
        return main

    def worktree(self, issue="7", branch="f/7-add-thing", **state):
        """A main checkout on `main` plus a linked worktree on the factory branch, as `fx shape` leaves it."""
        main = self.main_repo()
        wt = self.tmp / "wts" / f"{issue}-add-thing"
        git(main, "worktree", "add", "-q", "-b", branch, str(wt))
        ctx = fl.repo_ctx(wt)
        fl.save_state(ctx, issue=issue, branch=branch, phase=state.pop("phase", "build"), tier=state.pop("tier", 1), **state)
        return main, wt, fl.repo_ctx(wt)

    def fx_in(self, cwd, fake, *argv, verify=None):
        patches = [mock.patch.object(fx, "run", fake), mock.patch.object(fx, "launch", return_value=0)]
        if verify is not None:
            patches.append(mock.patch.object(fl, "run_verify", return_value=verify))
        with contextlib.ExitStack() as st, contextlib.chdir(cwd):
            launch = [st.enter_context(p) for p in patches][1]
            code, out, err = call(*argv)
        return code, out, err, launch

    OK = {"ok": True, "failed": [], "reason": "ok", "tail": "", "lines": [], "seconds": 1.0, "over_budget": False}

    def test_ShipUpdatesOpenPr(self):
        _, wt, ctx = self.worktree()
        write(ctx["work"] / "packet.md", PACKET)
        fake = FakeRun({"gh pr list": [{"number": 12, "url": "https://github.com/o/r/pull/12"}]})
        code, out, err, _ = self.fx_in(wt, fake, "ship", "7", verify=self.OK)
        self.assertEqual(code, 0, err)
        self.assertTrue(fake.has("git", "push", "-u", "origin", "f/7-add-thing"))
        self.assertTrue(fake.has("gh", "pr", "edit", "12", "-R", "o/r", "--body-file"))
        self.assertFalse(fake.has("gh", "pr", "create"))
        self.assertTrue(any("ledger.py" in c[1] and "append" in c for c in fake.calls))
        self.assertEqual((ctx["work"] / "pr_body.md").read_text("utf-8"), PACKET + "\nCloses #7\n")
        st = fl.load_json(ctx["work"] / "state.json")
        self.assertEqual((st["ship"], st["pr"]), ("ok", "https://github.com/o/r/pull/12"))
        self.assertIn("Next: wait for CI", out)

    def test_ShipRefusesBadPacketAndRedVerify(self):
        _, wt, ctx = self.worktree()
        write(ctx["work"] / "packet.md", "## Intent\nx\n")
        code, _, err, _ = self.fx_in(wt, FakeRun(), "ship", "7", verify=self.OK)
        self.assertEqual(code, 1)
        self.assertIn("missing heading '## Acceptance checks'", err)
        self.assertIn("Next: fix packet.md, then fx ship 7", err)
        write(ctx["work"] / "packet.md", PACKET)
        red = self.OK | {"ok": False, "failed": ["unit"], "reason": "fail", "tail": "1 failed"}
        fake = FakeRun()
        code, _, err, _ = self.fx_in(wt, fake, "ship", "7", verify=red)
        self.assertEqual(code, 1)
        self.assertIn("verify --full failed (fail: unit)\n1 failed", err)
        self.assertFalse(fake.has("git", "push"))
        self.assertEqual(fl.load_json(ctx["work"] / "state.json")["ship"], "failed:unit")
        self.assertEqual(self.log_lines()[-1]["hook"], "fx_ship")
        self.assertIs(self.log_lines()[-1]["verify_ok"], False)

    def test_BuildT1PullsSpecBlock(self):
        main, wt, ctx = self.worktree()
        block = "+++\nid = 7\ntier = 1\n+++\n## Goal\nx"
        fake = FakeRun({"gh issue view": {"title": "Add thing", "labels": [{"name": "tier:T1"}],
                                          "body": f"intro\n<!-- factory:spec -->\n{block}\n<!-- /factory:spec -->\n"}})
        write(ctx["work"] / "spec.md", "stale copy from an earlier build\n")
        code, _, err, launch = self.fx_in(main, fake, "build", "7")
        self.assertEqual(code, 0, err)
        self.assertEqual((ctx["work"] / "spec.md").read_text("utf-8"), block + "\n")  # always rewritten from the issue
        st = fl.load_json(ctx["work"] / "state.json")
        self.assertEqual((st["phase"], st["tier"], st["spec_path"]), ("build", 1, ".factory/work/7/spec.md"))
        w, prompt, phase = launch.call_args.args
        self.assertEqual((w["root"], phase), (wt, "build"))
        self.assertTrue(prompt.startswith("Implement the spec in .factory/work/7/spec.md, unattended"))
        self.assertIn("Keep .factory/work/7/progress.md current", prompt)
        self.assertIn("Run the reviews your setup requires for T1. Fix", prompt)  # no default review at T1
        self.assertIn("write a `## Blocker` section in .factory/work/7/progress.md", prompt)
        self.assertRegex("## Blocker\nq\n", fl.BLOCKER)  # the heading the prompt names is the one fx detects
        for ch in '<>|&%"':
            self.assertNotIn(ch, prompt)  # claude is a .CMD on Windows

    def test_BuildT0WritesIntent(self):
        main, _, ctx = self.worktree(tier=0)
        fake = FakeRun({"gh issue view": {"title": "Fix typo", "labels": [{"name": "tier:T0"}], "body": "In the README."}})
        code, _, err, launch = self.fx_in(main, fake, "build", "7")
        self.assertEqual(code, 0, err)
        self.assertEqual((ctx["work"] / "intent.md").read_text("utf-8"), "# Fix typo\n\nIn the README.\n")
        self.assertEqual(fl.load_json(ctx["work"] / "state.json")["tier"], 0)

    def test_BuildT2NeedsOnlyASpec(self):  # D23: no approval before the build; a T3 spec is approved on the PR
        main, wt, ctx = self.worktree(tier=2)
        fake = FakeRun({"gh issue view": {"title": "t", "labels": [{"name": "tier:T2"}], "body": ""}})
        code, _, err, launch = self.fx_in(main, fake, "build", "7")
        self.assertEqual(code, 1)
        self.assertIn("T2 needs a spec file under docs/specs with id = 7\nNext: fx shape 7", err)
        launch.assert_not_called()
        write(wt / "docs" / "specs" / "7-add-thing.md", "+++\nid = 7\ntier = 2\n+++\n## Goal\nx\n")
        code, _, err, launch = self.fx_in(main, fake, "build", "7")
        self.assertEqual(code, 0, err)
        self.assertFalse(fake.has("gh", "pr", "list"))  # no pin lookup
        self.assertIn("for T2; if it names none, one independent review in a fresh context", launch.call_args.args[1])
        self.assertEqual(fl.load_json(ctx["work"] / "state.json")["tier"], 2)

    def remote_head(self, main, branch="main"):  # as `git clone` leaves it: origin/HEAD -> the default branch
        git(main, "update-ref", f"refs/remotes/origin/{branch}", "HEAD")
        git(main, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{branch}")

    def test_ShapeRefusesAWorkspaceParent(self):  # D23: the kickoff splits a parent into slices
        main = self.main_repo(cfg='project = "ws"\n[workspace]\norder = ["api"]\n[workspace.repos.api]\nrepo = "o/api"\n')
        fake = FakeRun({"gh issue view": {"title": "Both", "labels": [{"name": "factory:parent"}], "body": ""}})
        code, _, err, launch = self.fx_in(main, fake, "shape", "7")
        self.assertEqual(code, 1)
        self.assertIn("#7 is a workspace parent: /factory:idea split it into slices\nNext: fx shape <key>#<slice>", err)
        launch.assert_not_called()

    def test_ShapeCreatesWorktreeAndState(self):
        main = self.main_repo()
        self.remote_head(main)
        fake = FakeRun({"gh issue view": {"title": "Add the Thing!", "labels": [{"name": "tier:T2"}],
                                          "body": "Intent.\nParent: o/ws#1\n"}})
        code, _, err, launch = self.fx_in(main, fake, "shape", "7")
        self.assertEqual(code, 0, err)
        wt = (main / ".." / "demo-worktrees").resolve() / "7-add-the-thing"
        self.assertEqual(fl.git(wt, "rev-parse", "--abbrev-ref", "HEAD"), "f/7-add-the-thing")
        st = fl.load_json(wt / ".factory" / "work" / "7" / "state.json")
        self.assertEqual({k: st[k] for k in ("issue", "repo", "branch", "parent", "phase", "tier")},
                         {"issue": "7", "repo": "o/r", "branch": "f/7-add-the-thing", "parent": "o/ws#1",
                          "phase": "shape", "tier": 2})
        self.assertEqual(launch.call_args.args[1:], ("/factory:shape 7", "shape"))
        self.assertTrue(fake.has("git", "fetch", "origin"))
        code, _, err, _ = self.fx_in(main, fake, "shape", "7")  # rerun reuses the worktree
        self.assertEqual(code, 0, err)
        self.assertEqual([c for c in fake.calls if c[0] not in ("git", "gh")], [])  # no setup key: nothing else runs

    def test_ShapeRunsSetupOncePerWorktree(self):
        main = self.main_repo(cfg='project = "demo"\nsetup = ["fx-setup-probe", "--frozen"]\n')
        self.remote_head(main)
        fake = FakeRun({"gh issue view": {"title": "Add the Thing", "labels": [], "body": ""}})
        code, out, err, launch = self.fx_in(main, fake, "shape", "7")
        self.assertEqual(code, 0, err)
        self.assertEqual([c for c in fake.calls if c[0] == "fx-setup-probe"], [["fx-setup-probe", "--frozen"]])
        self.assertIn("Setting up the worktree: fx-setup-probe --frozen", out)
        wt = (main / ".." / "demo-worktrees").resolve() / "7-add-the-thing"
        self.assertTrue(fl.load_json(wt / ".factory" / "work" / "7" / "state.json")["setup_done"])
        launch.assert_called_once()
        code, _, err, _ = self.fx_in(main, fake, "shape", "7")  # already set up: not run again
        self.assertEqual(code, 0, err)
        self.assertEqual(len([c for c in fake.calls if c[0] == "fx-setup-probe"]), 1)

    def test_ShapeSetupAcceptsOneString(self):
        main = self.main_repo(cfg='project = "demo"\nsetup = "fx-setup-probe --frozen"\n')
        self.remote_head(main)
        fake = FakeRun({"gh issue view": {"title": "Add the Thing", "labels": [], "body": ""}})
        code, _, err, _ = self.fx_in(main, fake, "shape", "7")
        self.assertEqual(code, 0, err)
        self.assertEqual([c for c in fake.calls if c[0] == "fx-setup-probe"], [["fx-setup-probe", "--frozen"]])

    def test_ShapeNoOverlayMasterDefault(self):  # D23: no overlay, a `master` default branch, no gitignore entry
        main = self.tmp / "plain"
        main.mkdir()
        git(main, "init", "-q", "-b", "master")
        write(main / "app.py", "x = 1\n")
        git(main, "add", "-A")
        git(main, "commit", "-q", "-m", "init")
        git(main, "remote", "add", "origin", "git@github.com:o/plain.git")
        self.remote_head(main, "master")
        git(main, "commit", "-q", "--allow-empty", "-m", "local work")  # the worktree starts from origin/HEAD, not HEAD
        fake = FakeRun({"gh issue view": {"title": "Add thing", "labels": [{"name": "tier:T1"}], "body": ""}})
        code, _, err, _ = self.fx_in(main, fake, "shape", "7")
        self.assertEqual(code, 0, err)
        wt = (main / ".." / "plain-worktrees").resolve() / "7-add-thing"
        self.assertEqual(fl.git(wt, "rev-parse", "HEAD"), fl.git(main, "rev-parse", "origin/master"))
        self.assertTrue((wt / ".factory" / "work" / "7" / "state.json").is_file())
        self.assertEqual(fl.git(wt, "status", "--porcelain"), "")  # the work dir is ignored through info/exclude
        code, _, err, _ = self.fx_in(main, fake, "shape", "7")
        self.assertEqual(code, 0, err)
        self.assertEqual((main / ".git" / "info" / "exclude").read_text("utf-8").count(".factory/work/"), 1)
        self.assertTrue((main / ".git" / "info" / "exclude").read_bytes().endswith(b"\n.factory/work/\n"))  # LF

    def test_ShapeRefusesEmptyRepo(self):
        main = self.main_repo()  # no origin/HEAD, and set-head --auto finds none (the remote has no commits)
        real = fl.git
        with mock.patch.object(fl, "git", side_effect=lambda cwd, *a: None if a[:2] == ("remote", "set-head") else real(cwd, *a)):
            code, _, err, launch = self.fx_in(main, FakeRun({"gh issue view": {"title": "t", "labels": [], "body": ""}}),
                                              "shape", "7")
        self.assertEqual(code, 1)
        self.assertIn("o/r has no default branch yet (an empty repo)\nNext: push a first commit, then fx shape 7", err)
        launch.assert_not_called()

    def test_LaunchStartsABackgroundSession(self):  # D23: no model flags; fx returns at once and records the session
        main, wt, ctx = self.worktree()
        ok = mock.Mock(returncode=0, stdout="backgrounded \u00b7 3ed22d67\n  claude agents  list sessions\n", stderr="")
        with mock.patch.object(fx.subprocess, "run", return_value=ok) as r, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(fx.launch(ctx, "/factory:shape 7", "shape"), 0)
        self.assertEqual(r.call_args.args[0][1:], ["--bg", "/factory:shape 7"])
        self.assertEqual(Path(r.call_args.kwargs["cwd"]), wt)
        env = r.call_args.kwargs["env"]
        self.assertEqual(env["FACTORY_PHASE"], "shape")
        self.assertNotIn("CLAUDE_CODE_USE_POWERSHELL_TOOL", set(env) - set(os.environ))
        self.assertIn("Watch: claude attach 3ed22d67", out.getvalue())
        self.assertEqual(fl.load_json(ctx["work"] / "state.json")["sessions"], ["3ed22d67"])
        bad = mock.Mock(returncode=1, stdout="", stderr="Workspace not trusted. Run `claude` in X once.")
        ctx = fl.repo_ctx(wt)  # before the patch: repo_ctx runs git
        with mock.patch.object(fx.subprocess, "run", return_value=bad), self.assertRaises(fx.Refused) as e:
            fx.launch(ctx, "/factory:shape 7", "shape")
        self.assertIn("claude --bg did not start: Workspace not trusted.", str(e.exception))
        self.assertIn("Next: fix the cause above, then rerun fx shape", str(e.exception))

    def test_CleanNeedsMerged(self):
        main, wt, ctx = self.worktree(pr="https://github.com/o/r/pull/12", sessions=["aa11", "bb22"])
        write(ctx["work"] / "progress.md", "notes\n")
        fake = FakeRun({"gh pr view": {"state": "OPEN", "url": "https://github.com/o/r/pull/12"}})
        code, _, err, _ = self.fx_in(main, fake, "clean", "7")
        self.assertIn("is open, not merged\nNext: the owner merges it, then fx clean 7", err)
        fake.gh["gh pr view"]["state"] = "MERGED"
        real, calls, listed = fx.subprocess.run, [], iter(['[{"id": "aa11"}, {"name": "other"}]', "[]"])
        def run(cmd, *a, **k):  # claude calls are recorded (agents lists aa11 once, then nothing); git runs for real
            if "claude" not in Path(cmd[0]).name.lower():
                return real(cmd, *a, **k)
            calls.append(cmd[1:])
            return mock.Mock(stdout=next(listed) if cmd[1] == "agents" else "")
        with mock.patch.object(fx.subprocess, "run", run), mock.patch.object(fx.time, "sleep") as nap:
            code, out, err, _ = self.fx_in(wt, fake, "clean", "7")  # from inside the worktree
        self.assertEqual(code, 0, err)
        self.assertEqual(calls, [["stop", "aa11"], ["stop", "bb22"], ["agents", "--json"], ["agents", "--json"]])
        nap.assert_called_once()  # waited until the stopped sessions were gone
        self.assertFalse(wt.exists())
        self.assertIsNone(fl.git(main, "rev-parse", "--verify", "--quiet", "refs/heads/f/7-add-thing"))
        self.assertEqual((self.home / "archive" / "o" / "r" / "7" / "progress.md").read_text("utf-8"), "notes\n")

    def test_StatusEndToEnd(self):
        main, wt, ctx = self.worktree(ship="failed:unit")
        fake = FakeRun({"gh pr list": []})
        code, out, err, _ = self.fx_in(main, fake, "status")
        self.assertEqual(code, 0, err)
        self.assertIn("7      T1    build", out)
        self.assertIn("Waiting on you:\n- 7: ship failed at unit. Next: `fx build 7`", out)

    def test_BareFxCleansMergedCleanWorktrees(self):  # item 13: bare fx only; a dirty worktree stays and is listed
        u7, u8 = "https://github.com/o/r/pull/7", "https://github.com/o/r/pull/8"
        main, wt7, ctx7 = self.worktree(pr=u7)
        write(ctx7["work"] / "progress.md", "notes\n")
        wt8 = self.tmp / "wts" / "8-add-thing"
        git(main, "worktree", "add", "-q", "-b", "f/8-add-thing", str(wt8))
        fl.save_state(fl.repo_ctx(wt8), issue="8", branch="f/8-add-thing", phase="build", tier=1, pr=u8)
        write(wt8 / "wip.py", "x = 1\n")  # uncommitted work
        fake = FakeRun({"gh pr list": [pr(7, "f/7-add-thing", "MERGED", repo="r"), pr(8, "f/8-add-thing", "MERGED", repo="r")]})
        code, out, err, _ = self.fx_in(main, fake, "status")
        self.assertEqual(code, 0, err)
        self.assertTrue(wt7.exists())  # `fx status` never cleans
        code, out, err, _ = self.fx_in(main, fake)
        self.assertEqual(code, 0, err)
        self.assertFalse(wt7.exists())
        self.assertIn("Cleaned #7", out)
        self.assertEqual((self.home / "archive" / "o" / "r" / "7" / "progress.md").read_text("utf-8"), "notes\n")
        self.assertTrue(wt8.exists())
        self.assertIn("- `fx clean 8`", out)
        self.assertNotIn("`fx clean 7`", out)

    def test_VerifyPrintsTotal(self):
        _, wt, _ = self.worktree()
        with contextlib.chdir(wt):
            code, out, _ = call("verify", "--fast")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"\nok; total \d+s\n$")
        self.assertIn("factory-verify: lint PASS 1s", out)
