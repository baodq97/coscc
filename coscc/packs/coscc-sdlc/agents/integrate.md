---
# Not a stage: Gebo, opened by the app on a conflict. impl's ceilings, to lower once runs are recorded.
name: "Gebo"
glyph: "ᚷ"
model: {"id": "claude-sonnet-5-5[1m]", "effort": "medium"}
skills: ["integrate"]
tools: {"Read": "allow", "Glob": "allow", "Grep": "allow", "Write": "allow", "Edit": "allow", "NotebookEdit": "allow", "Bash": "allow"}
output: {"kind": "session", "version": 1, "by": "session", "purpose": "Hand the app the commits only a person can settle, each with why; `[]` when there is none.", "fields": {"needs_person": {"list": {"commit": "text", "why": "text"}}}}
trigger: {"engine": "integrate"}
ceilings: {"turns": 120, "usd": 8.0}
warning: "Integrating runs `git` and `gh` with the GitHub login already on this machine, and force-pushes (with a lease) to this unit's branch. That login reaches every repository its account can reach, not just this workspace. What it resolves is an agent's word, not a person's approval."
consequence: "Rebases this pull request with this machine's gh login; a conflict opens a paid session."
---
