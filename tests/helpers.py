"""Shared test fixtures: temp FACTORY_HOME, throwaway git repos with an overlay."""
import importlib.util, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "plugins" / "factory" / "lib"))
sys.path.insert(0, str(CORE / "ci"))
import factory_lib as fl  # noqa: E402


def fixture(name):
    """A fresh copy of tests/fixtures/gh/<name> (GitHub API JSON shapes)."""
    return json.loads((CORE / "tests" / "fixtures" / "gh" / name).read_text("utf-8"))

PASS_SCRIPT = '#!/bin/sh\necho "factory-verify: lint PASS 1s"\necho "factory-verify: unit PASS 2s"\n'


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args], cwd=cwd,
                   check=True, capture_output=True)


class FactoryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp = Path(self._tmp.name).resolve()
        self.home = self.tmp / "home"
        self._env = {k: os.environ.get(k) for k in ("FACTORY_HOME", "FACTORY_PHASE")}
        os.environ["FACTORY_HOME"] = str(self.home)
        os.environ.pop("FACTORY_PHASE", None)

    def tearDown(self):
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self._tmp.cleanup()

    def user_config(self, text):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "config.toml").write_text(text, "utf-8")

    def repo(self, branch="f/7-add-thing", cfg='project = "demo"\n', verify=PASS_SCRIPT, name="repo"):
        """A git repo on `branch` (origin github.com:o/r) with the overlay committed and .factory/work/ ignored."""
        root = self.tmp / name
        root.mkdir()
        git(root, "init", "-q", "-b", "main")
        git(root, "commit", "-q", "--allow-empty", "-m", "init")
        git(root, "checkout", "-q", "-b", branch)
        git(root, "remote", "add", "origin", "git@github.com:o/r.git")
        if cfg is not None:
            write(root / ".factory" / "config.toml", cfg)
        if verify is not None:
            write(root / ".factory" / "verify", verify)
        write(root / ".gitignore", ".factory/work/\n")
        write(root / "src" / "app.py", "x = 1\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "overlay")
        return root

    def build_ctx(self, spec=None, **kw):
        """repo() plus state.json phase=build (as `fx build` leaves it) and an optional work-dir spec.md."""
        ctx = fl.repo_ctx(self.repo(**kw))
        if spec is not None:
            write(ctx["work"] / "spec.md", spec)
        fl.save_state(ctx, phase="build", tier=1, spec_path=f".factory/work/{ctx['issue']}/spec.md")
        return ctx

    def log_lines(self):
        p = self.home / "logs" / "hooks.jsonl"
        return [json.loads(ln) for ln in p.read_text("utf-8").splitlines()] if p.exists() else []


def hook_path(name):
    return CORE / "plugins" / "factory" / "hooks" / f"{name}.py"


def load_hook(name):
    """Import a hook script in-process (it shares this factory_lib module, so tests can patch it)."""
    spec = importlib.util.spec_from_file_location(f"hook_{name}", hook_path(name))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_script(name, data, **env):
    """Run a hook as Claude Code does: JSON on stdin. -> (exit code, stdout, stderr)"""
    r = subprocess.run([sys.executable, str(hook_path(name))], input=json.dumps(data).encode(), capture_output=True,
                       env={**os.environ, **env})
    return r.returncode, r.stdout.decode("utf-8"), r.stderr.decode("utf-8")


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))  # LF, as committed
    return path
