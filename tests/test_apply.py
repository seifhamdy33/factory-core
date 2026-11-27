import contextlib, importlib.util, io, json, os

from tests.helpers import CORE, FactoryTest, write

_spec = importlib.util.spec_from_file_location("apply", CORE / "user" / "apply.py")
assert _spec and _spec.loader
apply = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apply)

FRAG = json.loads((CORE / "user" / "settings.fragment.json").read_text("utf-8"))
DENY = FRAG["apply"]["permissions"]["deny"]
OWNER = {"permissions": {"defaultMode": "auto", "deny": ["Read(./secret)", "Bash(gh pr merge*)"], "allow": ["Bash(ls)"]},
         "model": "opus", "modelSettings": {"claude-opus-5-5": {"effortLevel": "high"}}, "theme": "dark"}
AGENT = "---\nname: mechanical-editor\nmodel: haiku\ndescription: x\n---\nBody model: keep\n"


class Apply(FactoryTest):
    def setUp(self):
        super().setUp()
        self.claude = self.tmp / "claude"
        self._cc = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude)

    def tearDown(self):
        os.environ.pop("CLAUDE_CONFIG_DIR", None) if self._cc is None else os.environ.__setitem__("CLAUDE_CONFIG_DIR", self._cc)
        super().tearDown()

    def run_apply(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = apply.main(list(argv))
        return code, out.getvalue()

    def owner_setup(self, settings=OWNER):
        write(self.claude / "settings.json", json.dumps(settings, indent=4) + "\n")
        write(self.claude / "agents" / "mechanical-editor.md", AGENT)

    def backups(self):
        return sorted(p.name for p in self.claude.glob("settings.json.bak-factory-*"))

    def test_CheckReportsDriftAndChangesNothing(self):
        self.owner_setup()
        code, out = self.run_apply("--check")
        self.assertEqual(code, 1)
        self.assertIn("drift: permissions.deny += Read(**/.env*)", out)
        self.assertNotIn("Bash(gh pr merge*)", out)  # already present
        self.assertIn("mechanical-editor.md: model: sonnet", out)
        self.assertIn("config.toml from user/factory.toml", out)
        self.assertEqual(json.loads((self.claude / "settings.json").read_text("utf-8")), OWNER)
        self.assertEqual(self.backups(), [])

    def test_ApplyUnionsBacksUpAndIsIdempotent(self):
        self.owner_setup()
        code, out = self.run_apply()
        self.assertEqual(code, 0, out)
        got = json.loads((self.claude / "settings.json").read_text("utf-8"))
        self.assertEqual(got["permissions"]["deny"][:2], ["Read(./secret)", "Bash(gh pr merge*)"])  # owner order kept
        self.assertEqual(set(got["permissions"]["deny"]), set(DENY) | {"Read(./secret)"})
        self.assertEqual(got["permissions"]["deny"].count("Bash(gh pr merge*)"), 1)
        self.assertEqual({k: got[k] for k in OWNER if k != "permissions"}, {k: OWNER[k] for k in OWNER if k != "permissions"})
        self.assertEqual(got["permissions"]["allow"], ["Bash(ls)"])
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(json.loads((self.claude / self.backups()[0]).read_text("utf-8")), OWNER)
        agent = (self.claude / "agents" / "mechanical-editor.md").read_bytes().decode()
        self.assertEqual(agent, AGENT.replace("model: haiku", "model: sonnet"))
        self.assertIn("build_note", (self.home / "config.toml").read_text("utf-8"))
        self.assertEqual(self.run_apply("--check"), (0, "factory user config: no drift\n"))
        self.assertEqual(self.run_apply(), (0, "nothing to do\n"))
        self.assertEqual(len(self.backups()), 1)  # no change, no new backup

    def test_CheckOnlyKeysAreNeverWritten(self):
        self.owner_setup(OWNER | {"model": "sonnet", "modelSettings": {}})
        code, out = self.run_apply()
        self.assertEqual(code, 0)
        self.assertIn("check only (not changed): model is 'sonnet', want 'opus'", out)
        self.assertIn("modelSettings.claude-opus-5-5.effortLevel is None, want 'high'", out)
        got = json.loads((self.claude / "settings.json").read_text("utf-8"))
        self.assertEqual((got["model"], got["modelSettings"]), ("sonnet", {}))
        self.assertEqual(self.run_apply("--check")[0], 1)

    def test_ExistingFactoryConfigKept(self):
        self.owner_setup()
        write(self.home / "config.toml", "verify_slots = 4\n")
        self.run_apply()
        self.assertEqual((self.home / "config.toml").read_text("utf-8"), "verify_slots = 4\n")

    def test_AgentWithoutModelLineGetsOne(self):
        self.assertEqual(apply.set_agent_model("---\nname: m\n---\nbody\n", "sonnet"), "---\nname: m\nmodel: sonnet\n---\nbody\n")
        self.assertIsNone(apply.set_agent_model("no frontmatter", "sonnet"))

    def test_MissingAgentIsDrift(self):
        write(self.claude / "settings.json", json.dumps(OWNER))
        code, out = self.run_apply("--check")
        self.assertIn("mechanical-editor.md is missing or has no frontmatter", out)
        self.assertEqual(code, 1)

    def test_FactoryTomlParses(self):
        import tomllib
        cfg = tomllib.loads((CORE / "user" / "factory.toml").read_text("utf-8"))
        self.assertEqual((cfg["verify_slots"], cfg["lock_wait_s"]), (2, 300))
        self.assertTrue(cfg["session"]["build_note"].startswith("This is delegating mode"))
