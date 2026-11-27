import json, os, unittest

from tests.helpers import CORE, FactoryTest, fl, load_hook, run_script, write

ri = load_hook("reinject")
NOTE = "This is delegating mode: a spec exists; work to the finish line."


class Reinject(FactoryTest):
    def start(self, ctx, source):
        return ri.run({"cwd": str(ctx["root"]), "source": source, "session_id": "s1"})

    def test_StartupPrintsRules(self):
        self.user_config(f'[session]\nbuild_note = "{NOTE}"\n')
        ctx = self.build_ctx(spec="+++\nid = 7\n+++\n## Goal\nSPEC-BODY\n")
        out = self.start(ctx, "startup")
        self.assertEqual(out.splitlines(), [ri.CORE_LINE, NOTE,
                                            "Factory session: phase build, issue #7, spec .factory/work/7/spec.md."])
        rec = self.log_lines()[0]
        self.assertEqual((rec["hook"], rec["decision"], rec["phase"], rec["session_id"]), ("reinject", "startup", "build", "s1"))
        self.assertEqual(rec["plugin_root"], str((CORE / "plugins" / "factory").resolve()))  # SP1: which copy ran

    def test_ShapeHasNoBuildNote(self):
        self.user_config(f'[session]\nbuild_note = "{NOTE}"\n')
        ctx = self.build_ctx()
        fl.save_state(ctx, phase="shape")
        self.assertNotIn(NOTE, self.start(ctx, "startup"))

    def test_ResumeAddsSpecAndProgress(self):
        ctx = self.build_ctx(spec="+++\nid = 7\n+++\n## Goal\nSPEC-BODY\n")
        write(ctx["work"] / "progress.md", "## Done\nPROGRESS-BODY\n")
        for src in ("resume", "compact", "clear"):
            out = self.start(ctx, src)
            self.assertIn("SPEC-BODY", out)
            self.assertIn("--- .factory/work/7/progress.md ---\n## Done\nPROGRESS-BODY", out)

    def test_UnderTenThousandChars(self):
        ctx = self.build_ctx(spec="+++\nid = 7\n+++\n## Goal\nSPEC-START\n" + "s" * 30000 + "\nSPEC-END\n")
        write(ctx["work"] / "progress.md", "PROGRESS-START\n" + "p" * 30000 + "\nPROGRESS-LATEST\n")
        out = self.start(ctx, "compact")
        self.assertLess(len(out), 9000)
        self.assertTrue(out.startswith(ri.CORE_LINE))
        self.assertIn("SPEC-START", out)  # spec keeps its head
        self.assertNotIn("SPEC-END", out)
        self.assertIn("PROGRESS-LATEST", out)  # progress keeps its latest notes
        self.assertEqual(out.count(ri.MARK), 2)

    def test_WorkspaceParentShape(self):
        os.environ["FACTORY_PHASE"] = "shape"  # fx shape on a workspace parent: no worktree, no issue branch
        ctx = fl.repo_ctx(self.repo(branch="main-x", cfg='project = "ws"\n'))
        self.assertEqual(self.start(ctx, "startup").splitlines(), [ri.CORE_LINE, "Factory session: phase shape."])

    def test_NonFactoryNoop(self):
        self.assertEqual(self.start(fl.repo_ctx(self.repo()), "startup"), "")  # config, but no work dir or env
        self.assertEqual(self.start(fl.repo_ctx(self.repo(cfg=None, name="plain")), "resume"), "")
        self.assertEqual(self.log_lines(), [])

    def test_ScriptEndToEnd(self):
        ctx = self.build_ctx()
        code, out, err = run_script("reinject", {"cwd": str(ctx["root"]), "source": "startup"})
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith(ri.CORE_LINE))


class HooksJson(unittest.TestCase):
    def test_Shape(self):
        hooks = json.loads((CORE / "plugins" / "factory" / "hooks" / "hooks.json").read_text("utf-8"))["hooks"]
        stop, = hooks["Stop"]
        self.assertEqual(stop["hooks"][0]["timeout"], 960)
        self.assertIn('hooks/stop_verify.py"', stop["hooks"][0]["command"])
        pre, = hooks["PreToolUse"]
        self.assertEqual(pre["matcher"], "Write,Edit,NotebookEdit")
        start, = hooks["SessionStart"]
        self.assertEqual(start["matcher"], "^(startup|resume|compact|clear)$")
        for entry in (stop, pre, start):
            cmd = entry["hooks"][0]["command"]
            self.assertTrue(cmd.startswith('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/'), cmd)


if __name__ == "__main__":
    unittest.main()
