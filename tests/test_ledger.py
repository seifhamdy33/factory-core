import contextlib, csv, io, json, os
from unittest import mock

from tests.helpers import CORE, FactoryTest, fl, write

import importlib.util

_spec = importlib.util.spec_from_file_location("ledger", CORE / "metrics" / "ledger.py")
assert _spec and _spec.loader
ledger = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ledger)

PACKET = "\n".join(f"{h}\n{'n/a' if 'Not verified' in h else 'x'}" for h in fl.DEFAULTS["packet"]["headings"].values()) + "\n"


def asst(mid, model="claude-opus-5-5", inp=10, out=5, cw=100, cr=1000, **kw):
    return {"type": "assistant", "message": {"id": mid, "model": model, "usage": {
        "input_tokens": inp, "output_tokens": out, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr}}, **kw}


class Ledger(FactoryTest):
    def setUp(self):
        super().setUp()
        self.cfgdir = self.tmp / "claude"
        self._cc = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.cfgdir)

    def tearDown(self):
        os.environ.pop("CLAUDE_CONFIG_DIR", None) if self._cc is None else os.environ.__setitem__("CLAUDE_CONFIG_DIR", self._cc)
        super().tearDown()

    def transcript(self, sid, lines, project="p", sub=None):
        p = self.cfgdir / "projects" / project / f"{sid}.jsonl"
        write(p, "".join((ln if isinstance(ln, str) else json.dumps(ln)) + "\n" for ln in lines))
        for name, sl in (sub or {}).items():
            write(p.parent / sid / "subagents" / f"{name}.jsonl", "".join(json.dumps(x) + "\n" for x in sl))

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ledger.main(list(argv)), 0)
        return out.getvalue()

    def rows(self):
        with open(self.home / "metrics" / "ledger.csv", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def test_AppendCountsBranchWorktreeAndSubagents(self):
        root = self.repo()
        wt = str(root)
        here = {"gitBranch": "f/7-add-thing", "cwd": wt + os.sep + "src", "sessionId": "s1"}
        self.transcript("s1", [
            {"type": "user", "message": {"role": "user", "content": "Implement the spec in x"}, **here},
            asst("m1", **here), asst("m1", **here),  # the same message on two lines counts once
            asst("m2", model="claude-sonnet-5-5", **here),
            asst("side", isSidechain=True, **here),  # sidechain lines in a main file are skipped
            asst("m3", gitBranch="main", cwd=wt),  # another branch: not counted
            "not json", "[1, 2]"], sub={"agent-a": [asst("sa1", model="claude-haiku-5-5", isSidechain=True)]})
        self.transcript("s2", [asst("x1", gitBranch="f/7-add-thing", cwd=str(self.tmp / "elsewhere"))])
        self.transcript("s3", [asst("y1", gitBranch="f/7-add-thing", cwd=wt)], project="q",
                        sub={"agent-b": [asst("y2", model="claude-haiku-5-5")]})
        write(fl.repo_ctx(root)["work"] / "packet.md", PACKET)
        with mock.patch.object(ledger, "pr_tier", return_value=2):
            self.run_cli("append", "--repo", "o/r", "--issue", "7", "--tier", "1", "--branch", "f/7-add-thing",
                         "--worktree", wt, "--pr", "https://github.com/o/r/pull/12", "--parent", "o/ws#1")
        (row,) = self.rows()
        self.assertEqual({k: row[k] for k in ("repo", "issue", "tier", "tier_src", "sessions", "packet_ok", "parent")},
                         {"repo": "o/r", "issue": "7", "tier": "2", "tier_src": "label", "sessions": "2", "packet_ok": "true",
                          "parent": "o/ws#1"})
        tok = json.loads(row["tokens"])
        self.assertEqual(tok["claude-opus-5-5"], {"input": 20, "output": 10, "cache_creation": 200, "cache_read": 2000})
        self.assertEqual(tok["claude-haiku-5-5"]["input"], 20)  # subagents of s1 and s3
        self.assertEqual(set(tok), {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"})

    def test_ReappendReplacesRowAndDeclaredFallback(self):
        root = self.repo()
        args = ["append", "--repo", "o/r", "--issue", "7", "--tier", "1", "--branch", "b", "--worktree", str(root)]
        with mock.patch.object(ledger, "pr_tier", return_value=None):
            self.run_cli(*args, "--pr", "u1")
            self.run_cli(*args, "--pr", "u2")
            self.run_cli(*args, "--pr", "u1")
        self.assertEqual([(r["pr"], r["tier"], r["tier_src"], r["packet_ok"]) for r in self.rows()],
                         [("u2", "1", "declared", "false"), ("u1", "1", "declared", "false")])

    def test_PrTierParsesLabels(self):
        done = mock.Mock(returncode=0, stdout=json.dumps({"labels": [{"name": "tier:T1"}, {"name": "tier:T3"}, {"name": "x"}]}))
        with mock.patch.object(ledger.subprocess, "run", return_value=done):
            self.assertEqual(ledger.pr_tier("u"), 3)
        with mock.patch.object(ledger.subprocess, "run", side_effect=OSError):
            self.assertIsNone(ledger.pr_tier("u"))

    def test_Summary(self):
        write(self.home / "metrics" / "ledger.csv", "date,repo,issue,tier,tier_src,sessions,tokens,pr,packet_ok,parent\n"
              + "".join(f'd,o/{r},{i},{t},label,1,"{{""m"": {{""input"": {n}, ""output"": 0, ""cache_creation"": 0, ""cache_read"": 9}}}}",u{i},true,{p}\n'
                        for r, i, t, n, p in (("api", 1, 1, 100, ""), ("api", 2, 2, 300, "ws#1"), ("web", 3, 2, 500, "ws#1"),
                                              ("api", 4, 1, 200, ""), ("api", 5, 1, 900, ""))))
        write(self.home / "metrics" / "weekly.csv", "week,date,usage_pct\n2026-W48,2026-11-27,42\n2026-W49,2026-12-04,\n")
        out = self.run_cli("summary")
        self.assertIn("T1  3  200  900", out)
        self.assertIn("T2  2  400  500", out)
        self.assertIn("shipped=5 t2=2 t3=0 paired=1 packet_ok=5/5 declared_tier=0", out)
        self.assertIn("weekly /usage %: 2026-W48 42, 2026-W49 -", out)

    def test_Audit(self):
        log = [
            {"hook": "stop_verify", "session_id": "a", "decision": "block", "verify_ok": False},
            {"hook": "stop_verify", "session_id": "a", "decision": "allow", "reason": "green", "verify_ok": True},
            {"hook": "stop_verify", "session_id": "b", "decision": "allow", "reason": "blocker"},
            {"hook": "stop_verify", "session_id": "c", "decision": "block", "verify_ok": False},  # ended by cap
            {"hook": "stop_verify", "session_id": "d", "decision": "allow", "reason": "green", "verify_ok": False},  # red done
            {"hook": "reinject", "session_id": "e", "phase": "build"},  # build session with no stop line
            {"hook": "reinject", "session_id": "f", "phase": "shape"},
            {"hook": "test_lock", "decision": "deny"}, {"hook": "verify", "decision": "over_budget"},
            {"hook": "fx_ship", "repo": "o/r", "issue": "7", "decision": "ship", "verify_ok": True}]
        write(self.home / "logs" / "hooks.jsonl", "".join(json.dumps(x) + "\n" for x in log) + "garbage\n")
        self.transcript("g", [{"type": "user", "isMeta": True, "message": {"content": "meta"}},
                              {"type": "user", "message": {"content": [{"type": "text", "text": "Implement the spec in s.md"}]}}])
        self.transcript("h", [{"type": "user", "message": {"content": "hello"}}])
        wt = self.tmp / "wt-9"  # a build-phase worktree: a plain `claude --resume` there with no plugin is unguarded
        write(wt / ".factory" / "work" / "9" / "state.json", '{"phase": "build"}')
        src = str(wt / "src")
        self.transcript("i", [{"type": "user", "cwd": src, "message": {"content": "continue please"}}])
        self.transcript("j", [{"type": "user", "cwd": src, "message": {"content": "/factory:shape 9"}}])  # shape: no gate
        self.transcript("k", [{"type": "user", "cwd": src, "message": {"content":  # how a typed slash command is stored
            "<command-message>factory:shape</command-message>\n<command-name>/factory:shape</command-name>\n<command-args>9</command-args>"}}])
        self.transcript("a", [{"type": "user", "cwd": src, "message": {"content": "go on"}}])  # has stop lines
        write(self.home / "metrics" / "ledger.csv", "date,repo,issue,tier,tier_src,sessions,tokens,pr,packet_ok,parent\n"
              "d,o/r,7,1,label,1,{},u7,true,\nd,o/r,8,1,label,1,{},u8,true,\n")
        out = self.run_cli("audit")
        self.assertEqual(out.splitlines(), ["red_done=1", "unguarded_build_sessions=3", "shipped_red=1",
                                            "ended_by_cap_or_interrupt=1", "test_lock_denies=1", "over_budget=1",
                                            "unguarded sessions: e, g, i"])

    def test_AuditEmpty(self):
        self.assertIn("red_done=0", self.run_cli("audit"))
        self.assertIn("shipped=0", self.run_cli("summary"))
