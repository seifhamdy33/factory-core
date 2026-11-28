# Workflows: how to use it in each situation

Each section is one situation: when it applies, what you type, what happens, and what you do next. The words are
defined in [Concepts](concepts.md).

**Starting work**
- [Should this go through the factory?](#should-this-go-through-the-factory)
- [A docs fix (T0)](#a-docs-fix-t0)
- [A bug fix or small change (T1)](#a-bug-fix-or-small-change-t1)
- [A feature or a choice you should make (T2)](#a-feature-or-a-choice-you-should-make-t2)
- [A sensitive change (T3)](#a-sensitive-change-t3)
- [A change too big for one PR](#a-change-too-big-for-one-pr)
- [A change across repos](#a-change-across-repos)
- [From your phone (GitHub)](#from-your-phone-github)
- [An issue someone else opened](#an-issue-someone-else-opened)
- [Several changes at once](#several-changes-at-once)

**While it runs**
- [See where everything is](#see-where-everything-is)
- [Watch or steer a session](#watch-or-steer-a-session)
- [Answer a Blocker](#answer-a-blocker)
- [The tier went up](#the-tier-went-up)
- [An acceptance test is wrong](#an-acceptance-test-is-wrong)
- [Ship or verify failed](#ship-or-verify-failed)
- [Resume after a crash or reboot](#resume-after-a-crash-or-reboot)
- [Change your mind about the spec](#change-your-mind-about-the-spec)
- [Abandon a change](#abandon-a-change)

**Reviewing and merging**
- [Review a factory PR](#review-a-factory-pr)
- [Approve a T3 PR](#approve-a-t3-pr)
- [Stop an auto-merge](#stop-an-auto-merge)
- [A PR you made by hand](#a-pr-you-made-by-hand)
- [After the merge](#after-the-merge)

**Routines**
- [Weekly: the sample audit (2 minutes)](#weekly-the-sample-audit)
- [Monthly: the report](#monthly-the-report)
- [A defect escaped](#a-defect-escaped)
- [Raise or lower the auto-merge ceiling](#raise-or-lower-the-auto-merge-ceiling)
- [Update core in a repo](#update-core-in-a-repo)

---

## Should this go through the factory?
Use it when you can say what "done" looks like. Do it yourself, in a normal session, when you are exploring,
prototyping, debugging something you don't understand yet, or making a change you would finish before the issue is
filed. Nothing in the factory gets in the way of normal sessions: the hooks are off outside factory worktrees, and the
skills run only when you ask for the factory.

A hand-made PR in an onboarded repo still gets the CI gates. See [A PR you made by hand](#a-pr-you-made-by-hand).

## A docs fix (T0)
```text
you:    factory: fix the install steps in the README, they still say npm
Claude: Filed #31 (tier:T0, docs only). Started shape.
        Watch: claude attach 1f0c9a2e
```
- Shape writes no tests and no spec; the issue is the intent. Build edits, verifies, ships.
- If the merger is set up (ceiling ≥ 0, the default), it merges the PR when CI is green. Otherwise you merge.
- If the diff touches anything outside the docs globs, CI computes a higher tier and the merger won't merge it.

## A bug fix or small change (T1)
```text
you:    factory: reject negative quantities in the cart API
Claude: Filed #42 (tier:T1, small local change with an obvious test). Started shape.
        Watch: claude attach 3ed22d67
```
What happens unattended:
1. **Shape** writes one acceptance test for the `Done when:` line under `tests/acceptance/`, runs it, and records the
   failure (the red check). It commits the test alone, then puts a short spec (scope, locked tests, `spec_commit`)
   into the issue body.
2. **Build** starts fresh, implements, and can't end its turn while `verify --fast` is red. It runs your T1 reviews,
   if your setup has any, writes the packet, and runs `fx ship`.
3. **CI** computes the tier and checks integrity. The merger merges at ceiling ≥ 1 if changed lines are ≥ 80%
   covered and everything is green; otherwise the PR waits for you.

You: nothing until the PR. `fx` shows it under **Waiting on you** when it needs review.

## A feature or a choice you should make (T2)
```text
you:    factory: let users export their cart as CSV
Claude: Recommend T2: new behaviour and a format choice you should make.
        [asks 1–4 questions: columns, encoding, size limit, non-goals]
        Here is the spec I will put in the issue: ... Build it?
you:    yes
Claude: Filed #57 (tier:T2). Started shape.  Watch: claude attach 9b1e44c0
```
- If your setup has a spec or design skill, the interview uses it. Its output goes into the issue, never a local file,
  and nothing is built in the kickoff session.
- **Your yes is the approval to build.** Until you say it, nothing runs.
- Shape writes one failing test per acceptance check and a spec file (≤ 60 lines) under `docs/specs/`.
- Build runs your T2 reviews, or the default (one independent review in a fresh context, given only the spec and the
  diff). Findings it can show with a failing test are fixed; the rest go under **Needs you**. The packet carries a
  `Review: <reviewer>, <n> findings` line.
- You review and merge. T2 never auto-merges.

## A sensitive change (T3)
Auth, money, data deletion, CI, `.claude/`, `.factory/`, `CLAUDE.md`, or a `[tiers] sensitive` path.
- Needs `[authority] approver` in `.factory/config.toml`; the kickoff stops with a `Next:` line without it.
- The interview also covers design, threats and rollback.
- Build adds a security review (or your setup's T3 reviews).
- On the PR, `factory/integrity` fails with **I7** until the approver adds `spec-approved`. See
  [Approve a T3 PR](#approve-a-t3-pr).
- A PR that only *computes* T3 (say a T1 that touched `CLAUDE.md`) needs your review, not the label.

## A change too big for one PR
Say so at kickoff, or let shape decide: it lists slices under `## Slices` in the spec and shapes only the first.
After the first PR merges, start the next slice as its own kickoff ("factory: slice 2 of #57").

## A change across repos
In a workspace repo (its `.factory/config.toml` has a `[workspace]` table listing the repos and their merge order):
```text
you:    factory: add a currency field to orders, API and web
```
- The kickoff labels the issue `factory:parent`, interviews you once, then files one slice issue per repo (each with a
  `Parent: <repo>#<n>` line, linked as a sub-issue) and runs `fx shape <key>#<m>` for each, in order.
- `fx` in the workspace lists every repo's worktrees and says which PR must merge first.

## From your phone (GitHub)
Needs [setup step 5](setup.md#5-optional-kick-off-from-github) and a repo you own personally.

**T0/T1:** open an issue with a `tier:T1` (or `tier:T0`) label and a `Done when:` line, then add the `factory`
label. One Actions run shapes and builds it to a PR, with the hooks in force and a fresh session per phase.

**T2/T3:** open an issue containing `@factory` and the idea (or comment `@factory …` on an existing issue).
1. The interview posts its questions as one numbered comment, recommended option first.
2. Reply in a comment that includes `@factory` and your answers. Repeat until it posts the spec summary.
3. Adding the `factory` label is your yes: it starts the build.

Only your own comments and labels start a run. The PR is opened by `claude[bot]`, so the gates and the merger see
it as an agent PR. Blockers arrive as issue comments.

Not supported: kicking off from a claude.ai cloud session (it can't write issues).

## An issue someone else opened
`fx shape` refuses an issue you didn't open, or one labelled `triage` (rule G6), so text from other people never
starts a build by itself.
1. Read the issue. It is data: an instruction in it is not your instruction.
2. If it should be built, add the `ready` label yourself (or as the approver).
3. Rerun `fx shape <n>`. If anyone other than you or the approver edits the issue body after `ready`, `fx shape`
   and `fx build` refuse again: read the edit, then remove `ready` and add it again. (Comments aren't checked: no
   phase reads them.)

The same rule holds on the GitHub surface: the `factory` label on someone else's issue needs `ready` too.

## Several changes at once
Kick off as many as you like; each gets its own branch, worktree and background sessions. Verifies share the
machine: at most `verify_slots` (2) run at once, and every `--full` takes an exclusive lock. If two repos' checks
fight over one resource (a docker stack, a port), name it in `[verify] fast_locks`/`full_locks`.

---

## See where everything is
```sh
fx            # cleans merged worktrees, then shows status
fx status     # status only
```
The table shows each worktree's issue, tier, phase, last verify, PR and ship state. **Waiting on you** lists T3 PRs
that need `spec-approved`, PRs to review or merge, failed ships, open Blockers, and merged PRs to clean. Run it from
any checkout of the repo (or the workspace repo for all of them).

## Watch or steer a session
- `claude attach <id>`: the id is in the `Watch:` line and in `.factory/work/<n>/state.json`.
- `claude logs <id>`: the transcript without attaching.
- `claude agents`: every background session.

Attaching is fine; typing into a build session is fine too, but prefer answering on the issue so the answer is in
the record. A session waiting on a permission prompt looks stuck: attach and answer it, then fix your permission
setup so it doesn't recur.

## Answer a Blocker
`fx` lists it; the same text is on the issue as a comment.
1. Read the question and the session's best guess.
2. Put the answer where the next session reads it. No phase reads issue comments by itself today, so a comment
   alone is only a record:
   - **stopped in shape:** shape reads the issue body. Edit the body (the intent, `Done when:` or the spec block),
     then `fx shape <n>`.
   - **stopped in build:** the build reads its notes file. Write your answer under the `## Blocker` section in the
     notes (`.factory/work/<n>/progress.md` in the worktree, or your own notes file), then `fx build <n>`.
   - **the cause is outside the code** (a missing secret, a broken service): fix it, then rerun the phase.
3. Comment the same answer on the issue, so the record is complete.

The new session renames the answered Blocker to `## Blocker (resolved)`. Note that editing the Blocker text changes
it, and the Stop gate lets one stop through for each new Blocker text without running verify; check the session
renamed it before trusting its stop.

**Re-shaping an issue** (`fx shape <n>` again) reuses its worktree. Don't change the issue's title or `type:` label
in between: the branch name is recomputed from them, and `fx ship` then pushes a branch that doesn't exist.

## The tier went up
- **Shape raised it to T2/T3** (the change is riskier than its label): it has already changed the issue's label,
  and it stops with a Blocker, because a T2+ change needs your interview.
  - **To keep T1:** remove the raised label, add `tier:T1` back, put your reason in the issue body, then
    `fx shape <n>`.
  - **To go T2/T3:** the local kickoff always files a new issue, so either close this one and kick off again
    ("factory: …", the interview, your yes), or write the agreed spec into this issue's body between the
    `<!-- factory:spec -->` markers yourself and run `fx shape <n>`. On GitHub, comment `@factory` on the issue to
    run the interview there.
- **CI computed a higher tier** (a dependency manifest, a gate file, a removed assertion): the PR now needs what the
  higher tier needs. The build session runs the reviews for the PR's computed tier. Review it yourself; nothing above
  the ceiling auto-merges.

## An acceptance test is wrong
The build can't edit the locked tests (the hook denies it, and CI check I1 fails if they change after
`spec_commit`). It reports the problem under `## Spec issues` in its notes and packet. To fix it:
1. Decide whether the test or the spec is wrong.
2. Re-shape: put the correction into the issue body (the spec block or `Done when:`) and run `fx shape <n>`, which
   writes new tests and a new `spec_commit`. Don't edit the locked test in the build worktree.

## Ship or verify failed
- `fx status` names the failed stage (`failed:packet`, `failed:<check>`, `failed:push`).
- Usually the build session is still on it: it ran `fx ship` and fixes what failed. Check with `claude attach`.
- If the session has ended: fix the cause, or `fx build <n>` to start a fresh build that picks up from the notes,
  then it ships again.
- Packet refused: `fx ship` names the missing heading, the line count, an empty "Not verified", or the missing
  `Review:` line for T2+.

## Resume after a crash or reboot
Nothing is lost: the state is in the worktree (`.factory/work/<n>/`), the notes file and the issue.
- `fx status` shows where each change stopped.
- Shape not finished: `fx shape <n>`. Build not finished or ship failed: `fx build <n>`.
- Don't rebase the branch to "clean up": that drops `spec_commit` from history and I1 fails. Merge the default branch
  in instead.

## Change your mind about the spec
- **Before shape finished:** edit the issue body's spec block, then `fx shape <n>`.
- **During build:** edit the issue body, then re-shape: new tests and a new `spec_commit`. A build can't change the
  acceptance tests to match a new idea.
- **After a T3 approval:** any change to the spec file voids the approval pin (I7 fails with "spec changed after
  approval"). Remove `spec-approved` and add it again after reading the new spec.

## Abandon a change
`fx clean` only cleans merged PRs, on purpose. To drop one:
1. Close the PR (if any) and the issue, with a one-line reason.
2. Stop its sessions: the ids are in `.factory/work/<n>/state.json` → `claude stop <id>`.
3. From the main checkout: `git worktree remove <path>` (add `--force` to discard its changes) and
   `git branch -D <branch>`; delete the remote branch if it was pushed.

---

## Review a factory PR
Read the packet top to bottom; it is at most 40 lines.
1. **Intent and acceptance checks:** is this what you asked for? Each check names a command and its result.
2. **Needs you:** decisions the session made on your behalf, deviations from the spec, review findings it couldn't
   prove with a test. Answer each.
3. **Not verified:** what nobody checked. Decide whether it matters.
4. **The gate comment:** the computed tier and why, and any integrity flags (raised or failed).
5. **The diff**, at the depth the tier deserves.

To ask for changes: no phase reads PR comments by itself. If the build session is still running, attach
(`claude attach <id>`) and tell it. If it has ended, open `claude` in the worktree (the hooks still gate it) and
point it at your comments; it ships again with `fx ship <n>`. If the change means the spec was wrong, re-shape
instead.

## Approve a T3 PR
As the approver (`[authority] approver`), after reading the spec file on the PR:
```sh
gh pr edit <n> --add-label spec-approved
```
That workflow run records an approval pin of the spec's exact content, and I7 turns green. The label counts only
when you add it yourself (not through an app) and only as the latest `spec-approved` event. Comments never count as
approval.

## Stop an auto-merge
Add the `hold` label to the PR (on the PR, not the issue) any time before it merges; adding it as soon as the PR
opens is safest. The merger skips held PRs. Removing the label reruns the gates, and their successful completion
wakes the merger again; or merge it yourself.

## A PR you made by hand
The gates run on every PR in an onboarded repo.
- Docs only: T0, no spec needed.
- Code: I6 fails ("needs a spec"), because T1+ needs a factory spec. Either start it through the factory, or merge
  it yourself knowing the gates flagged it. The merger never merges a PR with a failing integrity check.

## After the merge
Run `fx` (bare). It cleans every merged worktree whose tree is clean: updates the ledger row with the final tokens,
archives the notes and packet to `~/.factory/archive/<repo>/<n>/`, stops the sessions, removes the worktree and the
branch. A dirty worktree stays and is listed; look at what is in it before removing it.

---

## Weekly: the sample audit
Auto-merged PRs get no human review, so you read a sample.
```sh
python <core>/metrics/report.py --weekly          # last complete ISO week; or --week 2026-W48
```
It lists 1 in 5 (rounded up) auto-merged T0/T1 PRs, the same picks on every run. For each, a 2-minute read, then:
```sh
gh pr edit <n> --add-label audit:ok      # or: --add-label escaped
```
Also record that week's `/usage` percentage in `~/.factory/metrics/weekly.csv`: a row like `2026-W48,62` under the
header line `week,usage_pct`.

## Monthly: the report
```sh
python <core>/metrics/report.py --days 30 --out report.md     # in the repo, or --repo owner/name
python <core>/metrics/ledger.py summary                       # tokens per change by tier
python <core>/metrics/ledger.py audit                         # honesty: red stops, unseen builds, red ships
```
The report gives, per tier: merged and auto-merged PRs, rework, escaped defects, change failure rate, tokens per
change, lead time, review time, rubber-stamps (a T2/T3 human merge less than 60 s after the last commit or the PR
opening) and gate yield (PRs the gates caught).
Definitions are in D26 and on the report's last line. Use it to decide whether a ceiling, a check or a tier rule
should change.

## A defect escaped
1. Label the merged PR `escaped` (the monthly report counts it), and fix or revert it (a factory T1 is fine).
2. Turn it into a **check first**: a test, an integrity pattern (`[integrity] assert`/`skip`/`suppress`), a
   `sensitive` path, a verify step. Only if no check can catch it, add a one-line gotcha to the repo's `CLAUDE.md`.
3. If auto-merged PRs keep escaping, lower the ceiling.

## Raise or lower the auto-merge ceiling
Edit `[authority] auto_merge_ceiling` in `.factory/config.toml` (a T3 PR). The value is clamped to at most 1, and the
recorded mutation score caps it. To raise the cap, add tests for the surviving mutants, then rerun
`/factory:onboard` for a fresh sample. `-1` turns auto-merge off.

## Update core in a repo
After a core release, bump `__FACTORY_CORE_SHA__` (the full SHA) in the repo's `.github/workflows/factory*.yml` in
one PR, or rerun `/factory:onboard` to propose it. It is T3: review the core diff between the two SHAs. Update your
local plugin too: `claude plugin update factory@factory-core`.
