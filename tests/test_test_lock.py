import json, os, unittest

from tests.helpers import FactoryTest, fl, load_hook, run_script, write

tl = load_hook("test_lock")
SPEC = '+++\nid = 7\ntier = 1\nspec_commit = "abc"\nlocked = ["tests/acceptance/**", "fixtures/golden.json"]\n' \
       'scope = ["src/**"{extra}]\n+++\n## Goal\nx\n'


class TestLock(FactoryTest):
    def setUp(self):
        super().setUp()
        self.ctx = self.build_ctx(spec=SPEC.format(extra=""))
        self.root = self.ctx["root"]

    def edit(self, path, tool="Write", key="file_path", cwd=None):
        data = {"cwd": str(cwd or self.root), "session_id": "s1", "tool_name": tool, "tool_input": {key: str(path)}}
        return tl.handle(json.dumps(data))

    def assertDenied(self, path, glob=None, **kw):
        code, msg = self.edit(path, **kw)
        self.assertEqual(code, 2, path)
        self.assertIn("Acceptance tests are locked (", msg)
        self.assertIn("`## Spec issues` in progress.md", msg)
        if glob:
            self.assertIn(f"matches {glob})", msg)

    def assertAllowed(self, path, **kw):
        self.assertEqual(self.edit(path, **kw), (0, ""), path)

    def test_LockedPathDenied(self):  # the brief's "test lock fires" fixture
        self.assertDenied(self.root / "tests" / "acceptance" / "test_login.py", "tests/acceptance/**")
        self.assertDenied(self.root / "fixtures" / "golden.json", "fixtures/golden.json")
        self.assertDenied(self.root / ".factory" / "work" / "7" / "spec.md")
        self.assertDenied(self.root / ".factory" / "work" / "7" / "state.json")
        self.assertDenied(self.root / ".factory" / "verify", ".factory/verify")
        self.assertAllowed(self.root / "src" / "app.py")
        self.assertAllowed(self.root / ".factory" / "work" / "7" / "progress.md")
        self.assertAllowed(self.root / ".factory" / "work" / "7" / "packet.md")
        rec = self.log_lines()[0]
        self.assertEqual((rec["hook"], rec["decision"], rec["path"]), ("test_lock", "deny", "tests/acceptance/test_login.py"))

    def test_AcceptanceGlobAndCommittedSpec(self):
        fl.save_state(self.ctx, spec_path="docs/specs/7-x.md")  # T2/T3: the committed spec file, no work spec
        (self.ctx["work"] / "spec.md").unlink()
        write(self.root / "docs" / "specs" / "7-x.md", SPEC.format(extra=""))
        self.assertDenied(self.root / "docs" / "specs" / "7-x.md", "docs/specs/7-x.md")
        self.assertDenied(self.root / "tests" / "acceptance" / "test_7_flow.py")
        self.assertAllowed(self.root / "docs" / "specs" / "8-other.md")

    def test_NotebookPathDenied(self):
        self.assertDenied(self.root / "tests" / "acceptance" / "a.ipynb", tool="NotebookEdit", key="notebook_path")

    def test_WindowsPathNormalized(self):
        p = str(self.root / "src" / ".." / "tests" / "acceptance" / "test_a.py")
        if os.name == "nt":  # hooks get native C:\ paths, maybe with another drive-letter case
            p = p.replace("/", "\\")
            p = p[0].swapcase() + p[1:]
        self.assertDenied(p, "tests/acceptance/**")
        self.assertDenied("tests/acceptance/rel.py", cwd=self.root / "src" / "..")  # relative to cwd
        self.assertAllowed("app.py", cwd=self.root / "src")

    def test_GateFileDeniedInBuild(self):
        self.assertDenied(self.root / ".github" / "workflows" / "ci.yml", ".github/**")
        self.assertDenied(self.root / "CLAUDE.md", "CLAUDE.md")
        self.assertDenied(self.root / ".claude" / "settings.json", ".claude/**")
        self.assertDenied(self.root / "api" / "CODEOWNERS", "**/CODEOWNERS")

    def test_GateFileInScopeAllowed(self):
        write(self.ctx["work"] / "spec.md", SPEC.format(extra=', ".github/**", ".factory/**"'))
        self.assertAllowed(self.root / ".github" / "workflows" / "ci.yml")
        self.assertDenied(self.root / ".factory" / "verify")  # explicitly locked even when in scope
        self.assertDenied(self.root / ".factory" / "config.toml")
        self.assertDenied(self.root / "CLAUDE.md")

    def test_OutsideRootAllowed(self):
        self.assertAllowed(self.tmp / "elsewhere" / "tests" / "acceptance" / "x.py")
        self.assertDenied(self.home / "config.toml", "~/.factory/**")
        self.assertDenied(self.home / "logs" / "hooks.jsonl")

    def test_ShapePhaseAllowed(self):
        fl.save_state(self.ctx, phase="shape")
        self.assertAllowed(self.root / "tests" / "acceptance" / "test_login.py")
        self.assertAllowed(self.root / ".github" / "workflows" / "ci.yml")

    def test_NoFilePathAllowed(self):
        self.assertEqual(tl.handle(json.dumps({"cwd": str(self.root), "tool_input": {}})), (0, ""))

    def test_ScriptEndToEnd(self):
        data = {"cwd": str(self.root), "tool_name": "Edit",
                "tool_input": {"file_path": str(self.root / "tests" / "acceptance" / "test_login.py")}}
        code, out, err = run_script("test_lock", data)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("Acceptance tests are locked (tests/acceptance/test_login.py", err)


if __name__ == "__main__":
    unittest.main()
