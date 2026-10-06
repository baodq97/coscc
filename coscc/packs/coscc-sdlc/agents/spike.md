---
# Holds Bash and the app writes spike.md from the reply: writing is held to its throwaway cwd.
# 80 turns doubles the 40 that stopped several; $8 is impl's ($0.032-0.055 a turn).
name: "Perthro"
glyph: "ᛈ"
model: {"id": "claude-opus-5-5[1m]", "effort": "medium"}
skills: ["write-spike"]
tools: {"Read": "allow", "Glob": "allow", "Grep": "allow", "Write": "allow", "Edit": "allow", "NotebookEdit": "allow", "Bash": "allow", "vault": "allow"}
input: {"artifacts": ["intent", "spec", "spike?"], "outputs": [], "answers": true, "findings": false, "data": ["mentions"]}
output: {"kind": "artifact", "version": 2, "by": "scratch", "fields": {"judgement": {"enum": ["ready", "not-ready"]}, "questions": {"list": {"n": "number", "text": "text", "recommendation": "text"}}, "verdicts": {"list": {"id": "U[0-9]+", "verdict": {"enum": ["holds", "fails"]}}}}}
trigger: {"state": "spike"}
ceilings: {"turns": 80, "usd": 8.0}
warning: "This step runs arbitrary code (`python`, `node`, `npm`, `uv`) under this process's user, in a throwaway directory the app deletes afterwards. Nothing is a sandbox: Claude Code's auto mode and the app's few hard blocks hold the session, and what they miss is not undone."
---
