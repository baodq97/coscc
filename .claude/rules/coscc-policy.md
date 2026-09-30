---
paths:
  - "coscc/agent/policy.py"
  - "tests/agent/test_policy.py"
---

# Things that break here

- No board step opens a `pr` or `ship` session: the app's machine pushes, opens and merges with
  the machine's `gh` login, so it reaches every repository that login reaches. What stands
  before a merge is the gate and a guard pinned to the head it read. There is no grant for
  either; the consequence line beside the button stays.
- A grant reads words and is a tripwire, not a sandbox: assume `python -c` walks past it. Do
  not add enforcement by wording.
- Reads are held to the unit's worktree and folder, yet prompts name artifacts by path and rely
  on that boundary allowing the read: narrowing it breaks them.
- `impl` can read a sibling repository and cannot be kept from writing it.
- A redirect target is resolved once, when checked: a later symlink swap is missed.
- Ceilings and who may change a model: `.claude/docs/coscc-settings.md`; the spike grant:
  `.claude/docs/coscc-spike.md`.
