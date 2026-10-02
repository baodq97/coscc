---
name: cos-status
description: Report where every unit of work in .cos/ stands and what is blocking each one. Use when someone asks what is in flight, what needs accepting, what to pick up next, or for the state of a particular work unit.
---

# Report work unit status

Ask `uv run python -m coscc.loop status` (`--json` for data) with the app's snapshot, as the
prompt gives it.
Reproduce the table as printed; do not recompute a cell from the files. Report every line
under `Problems`. Name the one next action per unit and stop: starting it was not asked.
With no units, name an intent. This skill writes nothing.
