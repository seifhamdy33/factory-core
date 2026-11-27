<!-- Packet template. Use this repo's [packet.headings] in place of the defaults below, add every [packet] extra
heading, keep it at or under [packet] max_lines (default 40), and delete this comment. fx ship adds "Closes #<id>".
Paired change (state.json has a `parent`): if a sibling slice's PR in another repo must merge first, name it under
Look here, e.g. "- <api PR url>: merge it first; this PR's contract check fails until then". -->
Tier: T<n> (declared T<a>; CI computes the final tier)
Review: /code-review medium, <n> findings
## Intent
<1–2 lines: what is true now that wasn't before>
## Acceptance checks
| AC | Command | Result |
|---|---|---|
| AC1 | `<command>` | PASS |
## What changed
- <area>: <one line>
## Deviations from spec
None.
## Test changes
None.
## New dependencies
None.
## Look here
- `<file>:<line>`: <why>
## Not verified
<what wasn't run or checked, and why>
## Rollback
<revert the PR, plus any data or flag steps>
