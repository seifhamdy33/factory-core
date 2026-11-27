"""Size budgets (spec: Size budgets; D16) and the stdlib-only rule (D2). Lines are physical lines."""
import ast, re, subprocess, sys, unittest

from tests.helpers import CORE

BUDGETS = {
    "plugins/factory/hooks/stop_verify.py": 150,
    "plugins/factory/hooks/test_lock.py": 80,
    "plugins/factory/hooks/reinject.py": 60,
    "plugins/factory/lib/factory_lib.py": 250,  # owner 2026-11-27: raised from 200 (Windows Job Object tree kill)
    "plugins/factory/skills/idea/SKILL.md": 30,
    "plugins/factory/skills/shape/SKILL.md": 120,
    "plugins/factory/skills/ship/SKILL.md": 40,
    "bin/fx": 340,  # owner 2026-11-27: raised from 220 (waiting list, pin check and ship mechanics)
    "bin/fx.cmd": 1,
    "ci/gates.py": 200,
    "ci/tier.py": 200,
    "ci/integrity.py": 200,
    "metrics/ledger.py": 150,
    "user/apply.py": 80,
}
CODE_DIRS = ("ci", "plugins/factory", "bin", "metrics", "user")
LOCAL_MODULES = {"factory_lib", "gates", "tier", "integrity"}
PILOT_NAMES = re.compile(r"livlyte|seif-livlyte|seifhamdy")


def code_files():
    out = []
    for d in CODE_DIRS:
        out += [p for p in (CORE / d).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return out


class Budgets(unittest.TestCase):
    def test_WithinBudget(self):
        for rel, limit in BUDGETS.items():
            with self.subTest(rel):
                n = len((CORE / rel).read_bytes().splitlines())
                self.assertLessEqual(n, limit, f"{rel}: {n} lines > budget {limit}. Ask the owner; don't raise it silently.")

    def test_StdlibOnly(self):
        allowed = set(sys.stdlib_module_names) | LOCAL_MODULES
        for p in code_files():
            if p.suffix != ".py" and p.name != "fx":
                continue
            with self.subTest(str(p.relative_to(CORE))):
                tree = ast.parse(p.read_text("utf-8"))
                mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
                mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and not n.level}
                self.assertEqual(mods - allowed, set())

    def test_NoPilotNamesInCore(self):  # D11: pilot specifics live in overlays, never in core code
        for p in code_files():
            with self.subTest(str(p.relative_to(CORE))):
                self.assertIsNone(PILOT_NAMES.search(p.read_text("utf-8", errors="replace")))

    def test_FilesAreLf(self):
        tracked = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", *CODE_DIRS, "tests"], cwd=CORE,
                                 capture_output=True, text=True, check=True).stdout.split()
        for rel in tracked:
            with self.subTest(rel):
                self.assertNotIn(b"\r\n", (CORE / rel).read_bytes())


if __name__ == "__main__":
    unittest.main()
