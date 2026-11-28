# Setup

Install once per machine, onboard once per repo, and optionally turn on the GitHub surface and auto-merge.

- [Prerequisites](#prerequisites)
- [Your Claude Code setup: what the factory assumes](#your-claude-code-setup-what-the-factory-assumes)
- [1. Install the plugin](#1-install-the-plugin)
- [2. Put fx on PATH](#2-put-fx-on-path)
- [3. Trust each repo once](#3-trust-each-repo-once)
- [4. Onboard a repo](#4-onboard-a-repo)
- [5. Optional: kick off from GitHub](#5-optional-kick-off-from-github)
- [6. Optional: auto-merge (the merger)](#6-optional-auto-merge-the-merger)
- [7. Optional: machine settings](#7-optional-machine-settings)
- [Keeping core up to date](#keeping-core-up-to-date)
- [Removing the factory from a repo](#removing-the-factory-from-a-repo)

## Prerequisites
- **Python ≥ 3.11** on PATH as `python3` (the hooks call it) and as `python` (`fx.cmd` calls it on Windows). On an
  older Python the hooks do nothing and `fx` refuses with a `Next:` line.
  - macOS: the system `python3` is 3.9; install 3.11+ (for example `brew install python@3.12`) and put it first.
  - Linux: the distribution's Python 3.11+ package.
  - Windows: the python.org installer; the `python3` WindowsApps alias works if it points at 3.11+.
- **git**, and the **GitHub CLI** `gh`, authenticated with access to the repos. Repos must be on **github.com**
  (`fx` refuses other hosts, GitHub Enterprise included).
- **Claude Code** with background sessions (`claude --bg`, `claude attach`, `claude agents`).
- A **POSIX `sh`** for `.factory/verify`. On Windows, Git for Windows provides it; `fx` falls back to
  `C:/Program Files/Git/bin/sh.exe`.

## Your Claude Code setup: what the factory assumes
The factory checks none of these and changes none of them. Set them up your own way.

| Assumption | Why | Example |
|---|---|---|
| Unattended sessions use a permission mode that doesn't prompt | A prompt in a background session waits until you attach | `auto` mode, or allow rules for your build and test commands |
| Something stops sessions from merging PRs | Local sessions use your `gh` login; only the merger should merge | A `Bash(gh pr merge:*)` deny rule, or a PreToolUse hook |
| Secret files are unreadable to sessions | Sessions read the repo freely | Deny rules on `.env*`, keys, cloud and `gh` credential files |
| Optional: a review rule per tier | Otherwise the core default applies (T2: one independent review given only the spec and the diff; T3: plus a security review) | A `CLAUDE.md` rule naming your reviewers |
| Optional: a spec or design workflow | Otherwise the kickoff interviews you in rounds of up to 4 questions | A spec skill |
| Optional: a handoff notes file | Otherwise notes go to `.factory/work/<n>/progress.md` | `[session] notes = ".claude-work/TASKS.md"` in `~/.factory/config.toml` |

## 1. Install the plugin
```sh
git clone https://github.com/<owner>/factory-core
claude plugin marketplace add <path-to>/factory-core
claude plugin install factory@factory-core
claude plugin list      # factory@factory-core should be listed and enabled
```

## 2. Put fx on PATH
Add `<core>/bin` to PATH. `bin/fx` is the Python script; `bin/fx.cmd` wraps it for Windows shells. Check with `fx`
in any repo: it prints a status table (empty at first).

## 3. Trust each repo once
Run `claude` once in the repo's main checkout and accept the trust prompt. Its worktrees are then trusted too.
`claude --bg` refuses an untrusted folder, so `fx shape` fails there with `Workspace not trusted`.

## 4. Onboard a repo
A repo works with no setup at all: the core defaults apply, the first shape session writes `.factory/verify`, and
there are no CI gates. Onboarding adds the gates, a verify script that matches the repo, and an auto-merge ceiling.

### With `/factory:onboard` (recommended)
In a `claude` session in the repo's main checkout:
```text
/factory:onboard
```
It reads the stack, test, lint and CI setup, asks only what the repo can't answer, and opens **one PR** on branch
`factory/onboard` with:
- `.factory/config.toml`: only the values that differ from the defaults, each with a reason;
- `.factory/verify`: the repo's real `--fast` and `--full` checks, plus the output of a `--fast` run;
- coverage upload steps in CI, if CI doesn't already upload an LCOV report;
- a mutation sample (up to 3 most-changed modules, at most 15 minutes) and the ceiling it allows;
- `.github/workflows/factory.yml` and `factory-merger.yml`, pinned to the current core SHA;
- optionally `factory-agent.yml` (it asks);
- the labels `spec-approved`, `hold`, `audit:ok`, `escaped` (create `ready`, `triage`, `factory` and, in a
  workspace, `factory:parent` yourself when you need them);
- a `tier:T3` "Factory onboarding" issue and a spec file scoped to exactly the files the PR changes.

**Your part:**
1. Read the PR: the overlay table, the verify output, the mutation result and the ceiling.
2. Do what its "only the owner can do" list says (the merger App, below, if you want auto-merge).
3. **First onboarding:** no gates run on this PR, because `factory.yml` isn't on the default branch yet. Read the
   spec and the diff yourself, then merge. The gates run on every PR after this one.
4. **A rerun on an onboarded repo:** the gates run. As the approver, add `spec-approved` after reading the spec;
   `factory/integrity` turns green; merge.

If the mutation score is under 40%, onboarding also opens an issue "Raise verification: mutation score <n>%" that
names the modules and the surviving mutants worth a test. Until the score is re-sampled, nothing auto-merges.

Rerun `/factory:onboard` at any time: on an onboarded repo it proposes only what changed (a new CI workflow, a new
stack, a fresh mutation sample).

### By hand
Make these changes in one PR and review it yourself. It is T3 (gate files), and the gates don't run on the PR that
adds their workflow.
1. Copy `plugins/factory/templates/config.toml` to `.factory/config.toml`. Set only what differs, typically
   `[authority] approver` (needed for T3) and `[tiers] sensitive`.
2. Copy `plugins/factory/templates/verify.sh` to `.factory/verify`, make it executable, and fill in the `--fast`
   checks (lint, typecheck, related tests, under ~5 minutes) and the `--full` checks.
3. Add `.factory/work/` to `.gitignore`.
4. Copy `plugins/factory/templates/factory.yml` to **exactly** `.github/workflows/factory.yml`. Replace
   `__FACTORY_CORE_REPO__` with `<owner>/factory-core` and `__FACTORY_CORE_SHA__` with a full commit SHA (approval
   pins are accepted only from runs of that exact path).
5. Create the labels: `gh label create spec-approved`, and `hold`, `audit:ok`, `escaped` if you use the merger. The
   kickoff creates missing `tier:` and `type:` labels itself.
6. Optional: `setup` (a dependency install `fx shape` runs once per worktree), `branch_types`, CODEOWNERS for gate
   files.

## 5. Optional: kick off from GitHub
Lets you start, interview and build from the GitHub web or mobile app. Needs a repo **you own personally** (for an
org repo nothing starts: the workflow checks the event's sender against the repo owner).
1. In a `claude` session in the repo, run `/install-github-app`. It installs the Claude GitHub app and stores the
   `CLAUDE_CODE_OAUTH_TOKEN` secret. Runs bill to your Claude subscription.
2. Copy `plugins/factory/templates/factory-agent.yml` to `.github/workflows/factory-agent.yml`; fill in
   `__FACTORY_CORE_REPO__` and `__FACTORY_CORE_SHA__` as for the gates. (Onboarding does this if you say yes.)
3. Create the `factory` label.

What the workflow does to keep repo code away from write tokens: the checkout keeps no credential
(`persist-credentials: false`), `fx` runs your `setup` command without `GH_TOKEN`, and the interview job can run
only `gh issue` and `gh label` commands. The build session still has Bash and the action's token, so it could push
to the default branch. To rule that out, add a branch ruleset that blocks direct pushes to the default branch.

How to use it: [From your phone](workflows.md#from-your-phone-github).

## 6. Optional: auto-merge (the merger)
The merger merges a PR without you when **all** of these hold:
- the PR is open, not a draft, and has no `hold` label;
- its author is an agent identity (`[authority] agents`; default: you, `claude[bot]`, `factory-agent[bot]`);
- the tier the merger recomputes itself (pinned core, the default branch's config, plus changed-line coverage) is at
  or under the ceiling;
- no integrity check fails;
- the branch is up to date with the base;
- every check run and status on the head is green, and both `factory/*` statuses are present.

A code change counts as T1 only if at least 80% of its added code lines are covered (`[coverage] changed_lines_min`);
otherwise, or with no coverage report, the merger treats it as T2.

Steps:
1. Create a GitHub App (Settings → Developer settings → GitHub Apps) with repository permissions **Contents**,
   **Pull requests** and **Issues: read and write**, and no webhook. Without Issues, its merges silently leave
   `Closes #n` issues open. Install it on the repo.
2. Store its client ID as the repo variable `MERGER_CLIENT_ID` and a private key as the secret `MERGER_APP_KEY`.
3. Make the repo's CI (on `pull_request`) upload the LCOV report as the artifact `coverage` holding `lcov.info`, or
   set `[coverage] artifact` and `path` to what it uploads.
4. Copy `plugins/factory/templates/factory-merger.yml` to `.github/workflows/factory-merger.yml`. Fill in the core
   repo and SHA, and replace `__CI_WORKFLOW__` with the `name:` of every workflow that runs on pull requests: the
   merger waits for all checks, and only a listed workflow's completion wakes it.
5. Set the ceiling: `[authority] auto_merge_ceiling = 0` (docs only) or `1`, within what the mutation score allows.

A branch ruleset (require a PR and the `factory/*` and CI checks; block force pushes; the App as the only bypass) is
optional hardening. The factory doesn't need it.

## 7. Optional: machine settings
`~/.factory/config.toml` (template: `user/factory.toml`):

| Key | Default | Meaning |
|---|---|---|
| `verify_slots` | 2 | Verifies running at once across all worktrees on this machine |
| `lock_wait_s` | 300 | How long a verify waits for a slot or lock before `lock-timeout` |
| `[session] build_note` | `""` | One line added to every build session's context |
| `[session] notes` | `.factory/work/{issue}/progress.md` | Your handoff notes file, per worktree |

To record weekly usage for the monthly report, create `~/.factory/metrics/weekly.csv` with the header line
`week,usage_pct`, then add one row per week (for example `2026-W48,62`), by hand or from your statusline. Without the
header, the first row is read as the header and the report drops the rest.

The template `user/factory.toml` sets a sample `build_note`; clear it or write your own before you copy it.

## Keeping core up to date
- **Plugin (hooks, skills):** `git pull` in your core clone, then `claude plugin update factory@factory-core` and
  start a new session. `fx status` warns when the installed copy differs from the clone.
- **`fx`:** runs from your clone, so `git pull` is enough.
- **CI gates, merger, GitHub surface:** each repo pins core by SHA. A core change reaches a repo only when you bump
  the SHA in its `.github/workflows/factory*.yml`, in a PR (T3: you review it). Rerunning `/factory:onboard`
  proposes the bump.

## Removing the factory from a repo
Delete `.github/workflows/factory*.yml` and `.factory/` in one PR, and optionally the labels. Run bare `fx` first to
clean merged worktrees; unmerged ones need `git worktree remove` by hand. Machine data in `~/.factory/` stays until
you delete it.
