import contextlib, datetime as dt, importlib.util, io, os, tempfile, unittest
from pathlib import Path
from unittest import mock

from tests.helpers import CORE

_spec = importlib.util.spec_from_file_location("report", CORE / "metrics" / "report.py")  # as tests/test_ledger.py
assert _spec and _spec.loader
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)

NOW = dt.datetime(2026, 11, 28, 12, tzinfo=dt.timezone.utc)


def pr(n, tier, merged=None, created="2026-11-20T10:00:00Z", body="", labels=(), title=None):
    return {"number": n, "title": title or f"pr {n}", "body": body, "created_at": created, "merged_at": merged,
            "closed_at": merged or "2026-11-21T00:00:00Z", "updated_at": merged or "2026-11-21T00:00:00Z",
            "html_url": f"https://github.com/o/r/pull/{n}", "head": {"sha": f"sha{n}"},
            "labels": [{"name": f"tier:T{tier}"}, *({"name": x} for x in labels)]}


def routes():
    prs = [
        pr(1, 0, "2026-11-20T10:30:00Z", body="Closes #404", labels=("audit:ok",)),  # auto-merged docs; issue gone
        pr(2, 1, "2026-11-21T12:00:00Z", body="Closes #9"),  # auto-merged, one commit after opening
        pr(3, 2, "2026-11-22T10:00:30Z", created="2026-11-22T10:00:00Z", body="Fixes #8", labels=("escaped",)),  # human, 30 s after the last commit
        pr(4, 2, None, body="Closes #7"),  # closed unmerged: the gates raised it (declared T1)
        pr(5, 1, "2026-11-23T10:00:00Z", created="2026-11-23T09:30:00Z", title='Revert "pr 2"'),  # change before open,
        # then an update-branch merge commit at 09:45: neither rework nor the start of review
        pr(6, 1, "2026-10-01T10:00:00Z"),  # before the window
    ]
    r = {"pulls?state=closed&sort=updated&direction=desc&per_page=100&page=1": prs,
         "issues/9": {"created_at": "2026-11-21T00:00:00Z", "labels": [{"name": "tier:T1"}]},
         "issues/8": {"created_at": "2026-11-22T08:00:00Z", "labels": [{"name": "tier:T2"}]},
         "issues/7": {"created_at": "2026-11-20T00:00:00Z", "labels": [{"name": "tier:T1"}]},
         "commits/sha4/status": {"statuses": [{"context": "factory/integrity", "state": "success"}]}}
    for n, by, commits in ((1, "Bot", ["2026-11-20T09:00:00Z"]), (2, "Bot", ["2026-11-20T09:00:00Z", "2026-11-21T11:00:00Z"]),
                           (3, "User", ["2026-11-22T10:00:00Z"]), (5, "User", ["2026-11-23T09:00:00Z", "merge 2026-11-23T09:45:00Z"])):
        r[f"pulls/{n}"] = {"merged_by": {"type": by}}
        r[f"pulls/{n}/commits?per_page=100"] = [{"commit": {"author": {"date": d.split()[-1]}},
                                                 "parents": [{}, {}] if d.startswith("merge") else [{}]} for d in commits]
    return r


def fake_api(r):
    def api(path):
        if path == "issues/404":
            raise report.GhError("report: gh api issues/404 failed: Not Found")
        return r.get(path)
    return api


class Report(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        home = Path(self._tmp.name)
        (home / "metrics").mkdir()
        (home / "metrics" / "ledger.csv").write_text(
            "date,repo,issue,tier,tier_src,sessions,tokens,pr,packet_ok,parent\n"
            '2026-11-21,o/r,9,1,label,2,"{""m"": {""input"": 10, ""output"": 20, ""cache_creation"": 70, ""cache_read"": 5}}",'
            "https://github.com/o/r/pull/2,true,\n", "utf-8")
        p = mock.patch.dict(os.environ, {"FACTORY_HOME": str(home)})
        p.start()
        self.addCleanup(p.stop)

    def run_report(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            report.main(["--repo", "o/r", *args], api=fake_api(routes()), now=NOW)
        self.err = err.getvalue()
        return out.getvalue()

    def row(self, text, tier):
        return [c.strip() for c in next(ln for ln in text.splitlines() if ln.startswith(f"| {tier} |")).split("|")[1:-1]]

    def test_Monthly(self):
        text = self.run_report()
        self.assertIn("# factory report: o/r, 2026-10-29 to 2026-11-28", text)
        self.assertEqual(self.row(text, "T0"), ["T0", "1", "1", "0% (0/1)", "0", "0% (0/1)", "-", "-", "-", "0",
                                                "0 of 1 closed"])
        t1 = self.row(text, "T1")  # #2 (rework, reverted by #5, 100 tokens, lead 12 h) and #5 (human, 30 min)
        self.assertEqual(t1, ["T1", "2", "1", "50% (1/2)", "0", "50% (1/2)", "100", "12.0", "30.0", "0", "0 of 2 closed"])
        t2 = self.row(text, "T2")  # #3 escaped, a 30 s human merge (rubber-stamp); #4 raised above its issue's T1
        self.assertEqual(t2, ["T2", "1", "0", "0% (0/1)", "1", "100% (1/1)", "-", "2.0", "0.5", "1", "1 of 2 closed"])
        self.assertIn("- G11 sample audit of auto-merged PRs: 1 `audit:ok`, 0 `escaped`, 1 not sampled or unmarked.", text)
        self.assertIn("none recorded (no weekly.csv)", text)
        self.assertIn("issues/404 failed: Not Found (counted as unknown)", self.err)  # one lookup fails, the report runs

    def test_WeeklyFromGitHub(self):
        text = self.run_report("--weekly")  # default: the last complete ISO week, 2026-W47 (Nov 16-22)
        self.assertEqual(text, self.run_report("--weekly", "--week", "2026-W47"))
        self.assertIn("2026-W47 (1 of 2 auto-merged T0/T1)", text)  # #1 and #2; #3 was a human's, #5 is in W48
        self.assertRegex(text, r"(?m)^- #(1 pr 1 \(T0, audit:ok\)|2 pr 2 \(T1, unmarked\)): https://")
        self.assertIn("(0 of 0 auto-merged T0/T1)", self.run_report("--weekly", "--week", "2026-W48"))

    def test_WeeklySampleIsSeeded(self):  # G11: 1 in 5, rounded up, the same picks for the same repo and week
        fs = [{"n": n, "title": f"pr {n}", "tier": n % 2, "merged": True, "auto": n != 3, "labels": set(), "url": ""}
              for n in range(1, 12)]
        picks = lambda text: [ln.split()[1] for ln in text.splitlines() if ln.startswith("- #")]  # noqa: E731
        week = picks(report.weekly("o/r", fs, "2026-W48"))
        self.assertEqual(len(week), 2)  # 10 auto-merged -> 2
        self.assertNotIn("#3", week)
        self.assertEqual(week, picks(report.weekly("o/r", fs, "2026-W48")))
        more = fs + [{**fs[0], "n": 12}]  # a PR merged later in the week keeps the earlier picks in the sample
        self.assertTrue(set(week) <= set(picks(report.weekly("o/r", more, "2026-W48"))))
        later = [picks(report.weekly("o/r", fs, f"2026-W{w}")) for w in range(49, 54)]
        self.assertTrue(any(p != week for p in later))  # a new week draws again
