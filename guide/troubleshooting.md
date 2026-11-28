# Troubleshooting, limits and gotchas

- [Symptoms and fixes](#symptoms-and-fixes)
- [Merger: why didn't it merge?](#merger-why-didnt-it-merge)
- [Limits you should know](#limits-you-should-know)
- [Things that surprise people](#things-that-surprise-people)
- [Windows](#windows)

## Symptoms and fixes
| Symptom | Fix |
|---|---|
| `fx …` refused with a cause and `Next:` | Do what the `Next:` line says |
| `claude --bg did not start: Workspace not trusted` | Run `claude` once in the repo's main checkout and accept the prompt, then rerun |
| A background session seems stuck | `claude attach <id>` (from the `Watch:` line or `state.json`). It is usually waiting on a permission prompt: answer it, then allow that command in your settings |
| `fx` lists a Blocker | [Answer a Blocker](workflows.md#answer-a-blocker) |
| `fx shape`/`fx build`: "the owner hasn't marked it ready" or "edited after it was marked ready" (G6) | Someone else opened the issue or edited its body, or it is labelled `triage`. Read it; if it should be built, add `ready` (remove and re-add if it is already on) |
| `fx`: "not in a github.com repo" | `fx` works only with github.com remotes |
| `fx ship` push fails after a re-shape | The issue's title or `type:` label changed, so `state.json` names a new branch. Restore the title or label, then `fx ship <n>` |
| `lock-timeout` | Another worktree's verify holds the slot or lock. Wait, or raise `verify_slots` in `~/.factory/config.toml` |
| `no-verify-script` | The repo has no `.factory/verify`: `fx shape` again (the shape session writes one), or add it in its own reviewed PR |
| `fx ship`: packet refused | It names the problem: a missing heading, over 40 lines, an empty Not verified (T1+). Fix `packet.md`, rerun `fx ship <n>` |
| `fx ship`: uncommitted changes | Verify checks the working tree but the push sends only commits. Commit them (or revert them), rerun `fx ship <n>` |
| Ship failed at a stage | `fx status` names it. Fix and rerun `fx ship <n>`, or `fx build <n>` to resume |
| I1 "locked files changed" or "`spec_commit` not in history" | The build edited a locked test, or the branch was rebased. Re-shape; never rebase a factory branch (merge the default branch in) |
| I1 "acceptance tests changed after the commit that added them" (T2) | After a re-shape: expected; review and merge it yourself. Otherwise the build changed a test: revert that change |
| I6 "needs a spec" on a hand-made PR | Expected for T1+ without a factory spec: start it through the factory, or keep it docs-only |
| I7 "no spec-approved label" / "no factory-pin" | As the approver, add `spec-approved` on the PR |
| I7 "spec changed after approval" | Read the new spec, then remove `spec-approved` and add it again |
| The PR's `tier:` label is higher than the issue's | CI computed a higher tier; the comment says why. See [The tier went up](workflows.md#the-tier-went-up) |
| Two gate comments on one PR | Fixed in core (duplicates are deleted after each run); bump the repo's core pin |
| `fx` says the plugin differs | `claude plugin update factory@factory-core`, then start a new session |
| GitHub surface: nothing starts | The repo must be personal (not an org), the label or `@factory` comment must be yours, and `CLAUDE_CODE_OAUTH_TOKEN` must be set |
| A merged auto-merge left its issue open | The merger App lacks **Issues: read and write**; add it and accept the new permission on the installation |
| `report.py`: "counted as unknown" warnings | A linked issue or status was deleted or moved; the row still counts, with that field unknown |

## Merger: why didn't it merge?
Open the `factory-merger` workflow run for the PR's head. Its log has one line per PR:
```text
factory merger: #58 not merging: T2 > auto_merge_ceiling T1: computed T2: changed-line coverage 3/9: below 80%
```
| Reason | What to do |
|---|---|
| `author … is not an agent identity` | A PR by someone not in `[authority] agents`: merge it yourself |
| `the hold label is on` | Remove `hold`, or merge yourself |
| `T<n> > auto_merge_ceiling …` | Above the ceiling: review and merge yourself. "capped by the mutation score" means the score, not the setting, set the limit |
| `… no coverage artifact …` / coverage below 80% | Make CI upload `coverage`/`lcov.info` on `pull_request`, or add tests; meanwhile merge yourself |
| `integrity fails: I…` | Fix the flagged problem (see the gate comment) |
| `behind <base>: update the branch` | Update the branch (merge the base in); the next green run merges it |
| `checks not green: …` | Fix or rerun the named checks. A check from a workflow missing from the merger's `workflows:` list won't wake it: add it there |
| No run at all | The merger wakes only when a listed workflow completes **successfully**. Its `workflows:` must list the gates and every CI workflow by `name:`, and `MERGER_CLIENT_ID`/`MERGER_APP_KEY` must be set. A failed or cancelled CI run doesn't wake it: rerun CI |

## Limits you should know
- **Local sessions act as you.** There is no separate agent account (D25). Keeping sessions from merging, pushing to
  the default branch or reading secrets is your Claude Code setup's job (deny rules, hooks, permission mode). Without
  a branch ruleset, anything holding your credentials can push to the default branch and skip every gate.
- **I7 is a spec-drift signal, not a lock.** A `spec-approved` label added through an app is ignored, and sessions
  are told never to add it. But a session holding your login could add it as you, so a green I7 tells you the spec
  hasn't changed since the label, not that you read it. You still merge every T3 yourself.
- **Hooks and Bash deny rules are best-effort.** A script, `sh -c` or a full path can get around them. CI backs them up:
  I1's acceptance lock comes from the base branch's config and the PR's commits, but the `locked` list, `scope`
  and I7 read the spec on the PR branch, so they catch accidents, not a session set on gaming them. Read the
  locked tests' diff on any PR you review.
- **The statuses are information for human merges.** No ruleset requires `factory/*`, and any workflow with
  `statuses: write` could post one. The merger doesn't trust them: it recomputes the tier and integrity itself.
- **The first overlay PR runs no gates.** The workflow runs from the base branch, so there is nothing to run until it
  is merged. Review that PR by hand.
- **Not built yet:** dependency vulnerability or licence checks (a manifest change just raises to T2), packet lint in
  CI (only `fx ship` checks the packet), secret scanning, deploy or production automation, Sentry intake.
- **No phase acts on PR review comments by itself.** To get review feedback addressed, open `claude` in the
  worktree (still gated) and point it at the comments, or re-shape if the spec changes.
- **GitHub surface:** personal repos only; a 90-minute Actions limit for shape plus build; the plugin is loaded from
  the core's default branch, not the pinned SHA.

## Things that surprise people
- **Don't rebase a factory branch.** It drops `spec_commit` from history and I1 fails. Merge the default branch in.
- **Editing an approved T3 spec voids the approval.** Remove `spec-approved` and add it again.
- **A plain `claude` session in a build worktree is still gated.** The hooks read the phase from `state.json`.
- **Claude Code caps consecutive Stop blocks** (8 by default, `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`). A session that hits
  the cap ends red; `ledger.py audit` counts it as `ended_by_cap_or_interrupt`.
- **A finished background session stays idle until stopped,** and on Windows it holds the worktree folder. `fx clean`
  and bare `fx` stop the sessions `fx` started.
- **Keep `verify --fast` offline.** No env files, network or cloud; CI runs those checks.
- **Cleaning stops at a dirty worktree,** and deletes the branch only once GitHub reports the PR merged. Bare `fx`
  skips dirty ones; an explicit `fx clean <ref>` has already archived and stopped the sessions when it fails at the
  worktree removal.
- **Your own PRs auto-merge too.** The merger accepts PRs authored by the repo owner (that is the agent identity
  locally). Add `hold` to a hand-made PR you want to merge yourself, or narrow `[authority] agents`.

## Windows
- **Hooks call `python3`.** Usually the WindowsApps alias, which works if it points at Python ≥ 3.11. `fx.cmd` calls
  `python`.
- **`fx` keeps `<>|&%"` out of the prompts it passes,** because `claude` may resolve to an npm `.CMD` shim.
- **Keep files LF.** `.gitattributes` forces LF; commit with `core.autocrlf=false` if your git converts line endings.
- **A removed worktree's empty folder may stay** while a process still holds it; delete it after the process exits.
