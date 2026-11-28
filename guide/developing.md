# Developing factory-core

## Checks
```sh
python -m unittest discover -s tests -t . -q   # all tests
python -m unittest tests.test_budgets -q       # size budgets, stdlib-only, LF, no project names in core
pyright ci plugins bin metrics user            # types
claude plugin validate plugins/factory         # plugin manifest, skills, hooks
```
Core CI (`.github/workflows/ci.yml`) runs the tests on Linux, macOS and Windows with Python 3.11 and 3.13, and an
old-Python job that checks the hooks and `fx` refuse cleanly on 3.9.

## Rules
- **Size budgets.** Each component has a line budget in `tests/test_budgets.py`. Don't raise one without the owner's
  OK, and record the raise in `docs/decisions.md`.
- **No dependencies.** Hooks, gates, the merger and `fx` use the Python standard library only, or POSIX `sh`.
- **Generic core.** Project specifics belong in overlays. Core code must not name any project or account
  (`test_NoPilotNamesInCore`).
- **Thin shell.** Prompts say what, not how, and name a job, never a specific agent or model. Use the user's setup
  and add nothing to it.
- **Deterministic gates.** A CI check is pattern or diff logic. An LLM judgment never decides a status.
- **LF line endings.** `.gitattributes` forces them; commit with `core.autocrlf=false` on Windows.

## Layout
| Path | What |
|---|---|
| `.claude-plugin/marketplace.json` | The marketplace that serves the plugin |
| `plugins/factory/.claude-plugin/plugin.json` | Plugin manifest; bump `version` on each release |
| `plugins/factory/skills/{idea,shape,onboard}/SKILL.md` | The three skills |
| `plugins/factory/hooks/` | `hooks.json` and the three hooks |
| `plugins/factory/lib/factory_lib.py` | Shared by hooks, `fx` and CI: config, defaults, specs, verify, packet |
| `plugins/factory/templates/` | Overlay, verify, spec, packet and workflow templates |
| `bin/fx`, `bin/fx.cmd` | The CLI |
| `ci/gates.py`, `tier.py`, `integrity.py` | The gates; `ci/action.yml` is the composite action `factory.yml` calls |
| `ci/merger.py`, `ci/merge/action.yml` | The merger and its action |
| `metrics/ledger.py`, `metrics/report.py` | Machine-local ledger; GitHub report |
| `user/factory.toml` | Template for `~/.factory/config.toml` |
| `tests/` | One test module per component; `helpers.py` holds the fakes |

## Releases
1. Bump `version` in `plugins/factory/.claude-plugin/plugin.json`. Installed copies keep the old files until the
   version changes.
2. Merge to main. Users run `claude plugin update factory@factory-core`.
3. Each repo picks up `ci/` changes only when it bumps its pinned SHA (a T3 PR there).

## Add a stack
Stack patterns are plain lists: `ASSERT`, `SKIP` and `SUPPRESS` in `ci/integrity.py`, and `DEP_GLOBS` in
`ci/tier.py`. To support a stack in core, add its patterns there and a row to the table-driven test in
`tests/test_integrity.py`. For one repo only, set `[integrity] assert`/`skip`/`suppress` and `[tiers] deps` in its
overlay; they are added to the core lists, never replace them.

## Testing changes end to end
Use a throwaway repo you own, never a real project. Pin its workflows to your branch's SHA, then run a T1 kickoff
through to the merger, and a declared T3 through `spec-approved`.

## Design records
- `FACTORY_BRIEF.md`: the build brief, principles, phases, the gap register and the current status (section 5).
- `docs/decisions.md`, `docs/platform-facts.md`: decisions with their reasons, and verified Claude Code facts (a
  separate private repo, git-ignored here).
