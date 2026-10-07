---
# The one row whose ceilings are measured: 50 turns ended three of four impl steps mid-work;
# 120 is about twice the highest real attempt, $8 goes with it ($0.047 a turn).
# novel: 250 turns chosen; $16 is 2 x $8 (the dearest turn measured $0.0568 across impl runs).
name: "Uruz"
glyph: "ᚢ"
description: "Writes the code and tests the plan asks for."
model: {"id": "claude-sonnet-5-5[1m]", "effort": "medium", "trial": ["claude-opus-5-5[1m]", "claude-sonnet-5-5[1m]"]}
variants: {"novel": {"model": {"id": "claude-opus-5-5[1m]", "effort": "high"}, "ceilings": {"turns": 250, "usd": 16.0}}}
skills: ["write-impl"]
tools: {"Read": "allow", "Glob": "allow", "Grep": "allow", "Write": "allow", "Edit": "allow", "NotebookEdit": "allow", "Bash": "allow", "Agent": "allow", "SendMessage": "allow", "vault": "allow", "codegraph": "allow"}
helpers: ["scout", "worker"]
input: {"artifacts": ["intent", "spec?", "plan?"], "outputs": [], "answers": true, "findings": true, "data": ["plan-map", "drift", "siblings", "mentions"]}
output: {"kind": "artifact", "version": 3, "by": "session", "fields": {"judgement": {"enum": ["ready", "not-ready"]}, "questions": {"list": {"n": "number", "text": "text", "recommendation": "text"}}, "needs_person": {"list": "F[0-9]+"}, "left_lane?": "text"}}
ceilings: {"turns": 120, "usd": 8.0}
---
Builds what the accepted plan names and nothing beyond it.
