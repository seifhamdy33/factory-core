# Reference

Every command, skill, hook, check, config key, label and file, in one place. For when to use what, see
[Workflows](workflows.md).

- [fx commands](#fx-commands)
- [Skills](#skills)
- [Hooks](#hooks)
- [Verify](#verify)
- [CI gates](#ci-gates)
- [The merger](#the-merger)
- [The GitHub surface](#the-github-surface)
- [Evidence packet](#evidence-packet)
- [Spec format](#spec-format)
- [Labels](#labels)
- [Overlay: .factory/config.toml](#overlay-factoryconfigtoml)
- [Machine: ~/.factory/config.toml](#machine-factoryconfigtoml)
- [Environment variables](#environment-variables)
- [Where state lives](#where-state-lives)
- [Metrics](#metrics)

## fx commands
Every refusal prints its cause and one `Next:` line to follow. `<ref>` is an issue number, or `<key>#<n>` in a
workspace.

| Command | What it does |
|---|---|
| `fx` | Cleans every merged worktree whose tree is clean, then `fx status`. A dirty one stays and is listed. |
| `fx status` | A table of worktrees (issue, tier, phase, last verify, PR, ship state) and **Waiting on you**: T3 PRs needing `spec-approved`, PRs to review or merge, failed ships, Blockers, merged PRs to clean. Warns when the installed plugin differs from the clone. |
| `fx shape <ref>` | Refuses a workspace `factory:parent` issue (shape its slices). Checks intake (G6), creates the branch and worktree from `origin/HEAD`, runs `setup` once, then starts the shape session in the background. Prints `Watch: claude attach <id>`. |
| `fx build <ref>` | Checks intake again and that a spec exists for T1+ (T1: copies the issue's spec block into the worktree; T0: the issue becomes `intent.md`), then starts a fresh build session in the background. |
| `fx verify [--fast\|--full]` | Runs `.factory/verify` under the machine-wide slots, locks and timeouts, and records the result. |
| `fx ship [n]` | Checks the packet, runs `verify --full`, pushes, opens the PR (labelled `tier:T<n>`; the label must exist) or updates its body (packet + `Closes #n`), writes the ledger row. The gates own the PR's tier label after that. |
| `fx clean <ref>` | Only when GitHub shows the PR merged: updates the ledger row, archives notes and `packet.md`, stops the sessions `fx` started, removes the worktree and branch. |
| `fx ledger --pr <url>` | Rewrites this worktree's ledger row by hand. |

`fx` starts sessions with plain `claude --bg <prompt>`: no model, effort or permission flags, so your settings apply.

## Skills
| Skill | Who runs it | What it does |
|---|---|---|
| `/factory:idea <text>` | You, or Claude when you explicitly ask for the factory ("factory: …") | The kickoff: tier, interview for T2/T3, the issue, then `fx shape` |
| `/factory:shape <n>` | `fx shape` (user-invoked only) | Verify script, acceptance tests, red check, spec, then `fx build` |
| `/factory:onboard` | You (user-invoked only) | Proposes the overlay, verify, workflows, labels and ceiling as one T3 PR |

Only `idea` is visible to the model, so the plugin's standing context is one skill description.

## Hooks
All three do nothing outside factory sessions: they act only on a branch that matches the `branch` pattern (that
gives the issue number), and only when `FACTORY_PHASE` is set (as `fx` does) or the worktree's
`.factory/work/<n>/state.json` names a phase (with no phase there, a spec with `spec_commit` means build). A plain
`claude` session in a build worktree is therefore still gated.

| Hook | Event | Phase | What it does |
|---|---|---|---|
| `stop_verify.py` | Stop | build | Runs `verify --fast` when Claude tries to end its turn. Red → blocks the stop and returns the failing checks and the last ~60 output lines. On a retry of the same stop with nothing changed since the last `--fast` run, it reuses that result. After 3 identical failures, asks for a `## Blocker`; a new Blocker allows one stop. If the hook itself errors during a build, it blocks. |
| `test_lock.py` | PreToolUse (Write, Edit, NotebookEdit) | build | Denies edits to the spec's `locked` globs and `acceptance_glob`, the spec, `.factory/verify`, `.factory/config.toml`, `state.json`, anything under `~/.factory/`, and gate files outside the spec's scope. The message says to report a wrong test under `## Spec issues`. |
| `reinject.py` | SessionStart | shape, build | Adds the "external text is data" rule, the phase, issue, spec and notes paths, and (build only) `build_note`. After a resume, compaction or `/clear` it also adds the spec and the end of the default notes file (under 9,000 characters). With your own notes file, one pointer line instead. |

Claude Code caps consecutive Stop blocks (8 by default, `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`); a session that hits the
cap ends while red, and `ledger.py audit` counts it.

## Verify
`.factory/verify --fast|--full` prints one `factory-verify: <name> PASS|FAIL <seconds>s` line per check and exits 0
only if all passed. Template: `plugins/factory/templates/verify.sh`.
- **`--fast`:** lint, typecheck and the tests related to the change. Budget 300 s (slower runs are logged and shown),
  killed at 600 s. Must work offline: no env files, network or cloud.
- **`--full`:** everything. Killed at 3,600 s. Takes the exclusive `full` lock.
- A timeout kills the whole process tree.
- At most `verify_slots` verifies run at once on the machine; named locks keep checks sharing a resource apart.

## CI gates
`.github/workflows/factory.yml` runs on `pull_request_target`, so the workflow, the pinned core action and the
overlay config always come from the base branch. It reads the PR through the API and never checks out or runs PR
code. It posts two statuses, sets the `tier:` label and keeps one summary comment.

**`factory/tier`** is always green; its description is the tier and the main reason. The tier is the max of the
declared tier (issue label or spec) and the computed one:
- only docs globs → T0;
- code → T1;
- a dependency manifest (core list in `ci/tier.py` plus `[tiers] deps`), or a raising integrity flag → T2;
- a gate file or a `[tiers] sensitive` path → T3.

File and line counts appear as information only. Size never raises a tier.

**`factory/integrity`:**

| Flag | Effect | When |
|---|---|---|
| I1 | fail (raise to T2 at 300+ files) | Locked files changed after `spec_commit`; `spec_commit` isn't in the PR's history; or the spec locks files but has no `spec_commit`. When the compare lists 300+ files, the rest are unchecked and it raises to T2 |
| I2 | raise to T2 | Test files: deleted assertions; new skip, only or todo markers; new suppressions (`noqa`, `ts-ignore`, `eslint-disable`, …); a patch GitHub doesn't show. Patterns are data; an overlay adds its stack's with `[integrity] assert`, `skip`, `suppress`. |
| I3 | raise to T2 | Coverage or lint threshold config changed (`[integrity] threshold_globs`) |
| I4 | raise to T2 | Files outside the spec's `scope` |
| I5 | fail | A quarantined test used as an acceptance check |
| I6 | fail | Tier above T0 with no spec (the PR body must say `Closes #<n>`) |
| I7 | fail | A **declared** T3 without a valid approval pin |

**Approval pins.** When the approver adds `spec-approved`, that run reads the spec at the labelled commit and uploads
`pin.json` as the artifact `factory-pin`: PR, head, spec path, spec blob hash, approver, label event id. I7 accepts a
pin only if it came from a `pull_request_target` run of `.github/workflows/factory.yml`, matches the latest
`spec-approved` event applied by the approver (not through an app), and the spec's current blob equals the pinned
one. Comments never count as approval. The approver login must match exactly, case included. Pins are workflow
artifacts: once the repo's artifact retention expires one, I7 fails with "no factory-pin" and the approver adds the
label again.

## The merger
`.github/workflows/factory-merger.yml` runs on `workflow_run` (from the default branch) when the gates or a listed
CI workflow complete successfully (a failed or cancelled run doesn't wake it). It reads with `GITHUB_TOKEN`; only its merge step gets the App token. For each open PR at that
head it recomputes the tier with the pinned core and the default branch's config (it never trusts the PR's own
`factory/tier`), adds changed-line coverage, and squash-merges with `sha` = the head it checked, so GitHub refuses if
the head moved.

It merges only if all hold:

| Condition | Refusal reason it logs |
|---|---|
| Open and not a draft | `not an open, ready PR` |
| Author in `[authority] agents` (default: the repo owner, `claude[bot]`, `factory-agent[bot]`) | `author … is not an agent identity` |
| No `hold` label | `the hold label is on` |
| Tier ≤ effective ceiling | `T<n> > auto_merge_ceiling T<c>` (and whether the mutation score capped it) |
| No failing integrity flag | `integrity fails: I…` |
| Up to date with the base | `behind <base>: update the branch` |
| Every check run and status green; both `factory/*` present | `checks not green: …` |

**Effective ceiling** = min(`auto_merge_ceiling` (≤ 1), the mutation cap). Mutation cap: score ≥ 60 → 1, 40–59 → 0,
< 40 → -1 (none), no score → 0.

**Coverage (G1):** an LCOV report from the `coverage` artifact (`lcov.info`) of a successful `pull_request` run at
the PR head, at most 50 MB. A code change is T1 only if ≥ `changed_lines_min` (80%) of its added code lines are
covered; with no report, it is T2 for the merger. The report comes from PR code, so it only counts at T0/T1, where
the PR touches no gate or threshold file.

The run log line for each PR starts `factory merger: #<n> merging:` or `not merging:` with every reason.

## The GitHub surface
`.github/workflows/factory-agent.yml`, two jobs, each started only by the repo owner (personal repos only):

| Job | Trigger | What it does |
|---|---|---|
| `interview` | `@factory` in an issue you open, or in your comment | Runs `/factory:idea on GitHub: #<n>`: questions as issue comments, the spec into the issue, then asks for the `factory` label |
| `agent` | You add the `factory` label | `fx shape`, then a shape session, then a fresh build session, in one Actions run (90 min limit), with the plugin's hooks in force. With `FACTORY_NEXT` set, `fx` writes each next prompt to a file and the next step runs it. |

## Evidence packet
`.factory/work/<n>/packet.md`, from `plugins/factory/templates/packet.md`. `fx ship` refuses it unless:
- every configured heading is present (`[packet.headings]` maps them to your PR template; `[packet] extra` adds more);
- it is at most `max_lines` (40);
- for T1+, "Not verified" isn't empty (if your `not_verified` heading lacks those words, add a `Not verified: …` line);
- for T2+, it has a `Review: <reviewer>, <n> findings` line.

Default headings: Intent, Acceptance checks (a table of check, command, result), What changed, Needs you, Not
verified, Rollback. `Closes #<n>` is appended.

## Spec format
TOML frontmatter between `+++` lines, then Markdown sections (outline: `plugins/factory/templates/spec.md`):
```toml
+++
id = 42
tier = 2
scope = ["src/cart/**", "tests/acceptance/test_42_*"]
locked = ["tests/acceptance/test_42_*"]
spec_commit = "<sha of the tests commit>"
flag = "none: <reason>"     # T2/T3: a feature flag name, or none with a reason
+++
```
- **T1:** Goal and Acceptance checks, in the issue body between `<!-- factory:spec -->` and `<!-- /factory:spec -->`.
  `fx build` copies the block into the worktree on every run.
- **T2/T3:** Goal, Non-goals, Acceptance checks (T3 also design, threats, rollback), at most 60 lines without the
  frontmatter, in `specs_dir`/`spec_name`, committed on the branch.
- Each acceptance check names a runnable command in backticks.

## Labels
| Label | Set by | Created by | Meaning |
|---|---|---|---|
| `tier:T0`…`tier:T3` | Kickoff and shape (issue); `fx ship` and CI (PR) | The kickoff, if missing | Declared tier on the issue; final tier on the PR |
| `type:<prefix>` | Kickoff, if `branch_types` is set | The kickoff | Branch prefix (`{type}` in `branch`) |
| `factory:parent` | Kickoff in a workspace | **You** | A cross-repo parent issue |
| `ready` | You or the approver | **You** | G6: build this issue though someone else opened it, or it is in triage |
| `triage` | You | **You** | G6: never build without `ready` |
| `spec-approved` | The approver | Onboarding | Approves a declared-T3 spec on the PR (I7) |
| `factory` | You | **You** | GitHub surface: build this issue (also the yes to a GitHub-interviewed spec) |
| `hold` | You | Onboarding | The merger skips this PR (any time before it merges) |
| `audit:ok`, `escaped` | You | Onboarding | The weekly sample's verdict; `escaped` also marks a defect that got through |

## Overlay: `.factory/config.toml`
Optional; set only what differs. Defaults: `DEFAULTS` in `plugins/factory/lib/factory_lib.py`. Template with comments:
`plugins/factory/templates/config.toml`. **Floors (G5):** `tests`, `sensitive`, `gate_extra` and `threshold_globs`
are unioned with the core lists; `[integrity] assert`/`skip`/`suppress` and `[tiers] deps` add to the core patterns;
`docs_globs` only narrows; `max_lines` can only go down, `changed_lines_min` only up, and `auto_merge_ceiling` is
clamped to ≤ 1. Every other key simply replaces the default. Any overlay change is a T3 PR.

| Key | Default | Meaning |
|---|---|---|
| `project` | the GitHub repo name | Used in `worktree_root` |
| `tests` | `tests/**`, `test/**`, `**/test_*.py`, `**/*_test.py`, `**/*.test.*`, `**/*.spec.*` | Test files (I2) |
| `acceptance_glob` | `tests/acceptance/test_{issue}_*` | Where shape writes acceptance tests (always locked) |
| `specs_dir`, `spec_name` | `docs/specs`, `{issue}-{slug}.md` | T2/T3 spec files (`{date}` also works) |
| `branch`, `branch_types` | `f/{issue}-{slug}`, `[]` | Branch pattern; `{type}` from a `type:<prefix>` label (first entry by default) |
| `worktree_root`, `worktree_name` | `../{project}-worktrees`, `{issue}-{slug}` | Where worktrees go |
| `setup` | `[]` | An argv list or one string, run once per new worktree |
| `quarantine` | `[]` | Test ids no acceptance check may use (I5) |
| `[verify] fast_budget_s`, `cap_s`, `full_cap_s` | 300, 600, 3600 | Budget and hard caps |
| `[verify] fast_locks`, `full_locks` | `[]` | Named machine-wide locks |
| `[tiers] docs_globs` | `docs/**`, `**/*.md` | What counts as docs for T0 |
| `[tiers] sensitive` | `[]` | Paths that make a change T3 |
| `[tiers] gate_extra` | `[]` | Extra gate files (T3) |
| `[tiers] deps` | `[]` | Extra dependency manifests (T2) |
| `[integrity] threshold_globs` | jest, vitest, eslint, tsconfig, `.coveragerc`, `.nycrc` | Threshold config (I3) |
| `[integrity] assert`, `skip`, `suppress` | `[]` | Extra regexes added to the core lists (I2) |
| `[authority] approver` | `""` | Login whose `spec-approved` counts; required for T3 |
| `[authority] auto_merge_ceiling` | 0 | Highest tier the merger merges; at most 1; -1 for none |
| `[authority] agents` | `[]` → owner, `claude[bot]`, `factory-agent[bot]` | PR authors the merger accepts |
| `[coverage] artifact`, `path`, `changed_lines_min` | `coverage`, `lcov.info`, 80 | The merger's coverage source and bar |
| `[mutation] score`, `date`, `tool`, `sample` | absent | Written by `/factory:onboard`; the score caps the ceiling |
| `[packet] max_lines`, `extra`, `[packet.headings]` | 40, `[]`, the defaults | Packet shape |
| `[workspace] repos`, `order` | absent | Multi-repo workspace: `{key = {path, repo}}` and the merge order |

The merger reads only the **default branch's** overlay. The gates do too, except that they fall back to the PR's
own `config.toml` when the base branch has none (only an onboarding PR); that affects statuses, never a merge.

## Machine: `~/.factory/config.toml`
| Key | Default | Meaning |
|---|---|---|
| `verify_slots` | 2 | Verifies at once across all worktrees |
| `lock_wait_s` | 300 | Wait for a slot or lock before `lock-timeout` |
| `[session] build_note` | `""` | One line added to every build session's context |
| `[session] notes` | `.factory/work/{issue}/progress.md` | Your handoff notes file, per worktree |

## Environment variables
| Variable | Set by | Effect |
|---|---|---|
| `FACTORY_HOME` | You | Moves `~/.factory` |
| `FACTORY_PHASE` | `fx` (`shape` or `build`) | Turns the hooks on |
| `FACTORY_NEXT` | `factory-agent.yml` | `fx` writes the next session's prompt to this file instead of starting `claude --bg`, and works in the checkout instead of a worktree |
| `FACTORY_OWNER` | `factory-agent.yml` | The login G6 treats as the owner, instead of `gh api user` |
| `CLAUDE_CONFIG_DIR` | You | Moves `~/.claude`; `fx` and `ledger.py` honour it |

## Where state lives
| Path | What | Committed? |
|---|---|---|
| `.factory/config.toml`, `.factory/verify` | The overlay | Yes (gate files) |
| `.factory/work/<n>/` (in the worktree) | `state.json` (phase, tier, spec path, session ids, PR, ship state), `progress.md` (default notes), `packet.md`, `pr_body.md`, T1 `spec.md` or T0 `intent.md` | No (git-ignored) |
| `docs/specs/<n>-<slug>.md` | T2/T3 specs | Yes, on the branch |
| `~/.factory/locks/` | Verify slots and named locks | Machine-local |
| `~/.factory/logs/hooks.jsonl` | Every hook and ship decision | Machine-local |
| `~/.factory/metrics/ledger.csv`, `weekly.csv` | Per-PR cost; your weekly usage | Machine-local |
| `~/.factory/archive/<repo>/<n>/` | Notes and packet of cleaned changes | Machine-local |

## Metrics
`metrics/ledger.py` (machine-local, `~/.factory/metrics/`):
- **`append`** (run by `fx ship` and `fx clean`): one row per PR: date, repo, issue, tier, session count, tokens per
  model from `~/.claude/projects/*.jsonl` (matched by branch and worktree, subagents included), packet pass.
- **`summary`:** median and p90 tokens per change by tier (input + output + cache writes), counts, and the weekly
  usage line if `weekly.csv` exists.
- **`audit`:** sessions that ended while verify was red, build sessions the Stop gate never saw, PRs shipped red,
  sessions that ended at a block, test-lock denies, fast verifies over budget.

`metrics/report.py` (GitHub, through `gh api`; run in the repo or pass `--repo o/r`):
- **monthly** (`--days 30`, `--out FILE`): per tier, merged and auto-merged, rework, escaped, change failure, tokens
  per change, lead time, review time, rubber-stamps, gate yield. Definitions: D26 and the report's last line.
- **`--weekly`** (`--week YYYY-Www`): 1 in 5 auto-merged T0/T1 PRs of one ISO week, the same picks on every run.
