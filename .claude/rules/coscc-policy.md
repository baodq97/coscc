---
paths:
  - "coscc/agent/policy.py"
  - "tests/agent/test_policy.py"
---

# Things that break here

- No board step opens a `pr` or `ship` session: the app's machine pushes, opens and merges with
  the machine's `gh` login, so it reaches every repository that login reaches. Before a merge
  stand only the gate and a guard pinned to the head it read.
- Every session runs auto mode: the app's hook refuses the critical calls, the classifier
  judges the rest. A hook that raises lets the call through, so the gate refuses on any error.
- A `Grant` is one run's permission (`run.issue`), the only thing `critical` reads; a feature
  binds by catalog name and reads `facts.grant`, never a stage. The owner edits a row's tools, so
  no rule here trusts a row; `ask` is refused `asks-a-person`.
- The critical check reads words and is a tripwire, not a sandbox (`"gi"+"t"` walks past it).
  Keep it to the few calls that must never run.
- No read boundary: a session reads anything but the secrets; prompts name artifacts by path.
- A quoted word shaped like a command line is read again as one, except a commit message or a
  search pattern.
- Write tools are held to the grant's `write`; a command that writes is the classifier's.
  `impl` reads a sibling repository and a command can still write it.
- A path is resolved once, when checked: a symlink swap after is missed.
- Ceilings and who may change a model: `.claude/docs/coscc-settings.md`; the spike row:
  `.claude/docs/coscc-spike.md`.
