---
# Reads, and only reads: the app writes plan.md from the reply. 40/$4 chosen, not measured:
# 20 cut a plan mid-read at the ceiling.
name: "Raidho"
glyph: "ᚱ"
description: "journey: the right road in the right order"
model: {"id": "claude-opus-5-5[1m]", "effort": "medium"}
skills: ["write-plan"]
tools: {"Read": "allow", "Glob": "allow", "Grep": "allow"}
input: {"artifacts": ["intent", "spec?", "spike?", "plan?"], "outputs": [], "answers": true, "findings": false, "data": ["mentions"]}
output: {"kind": "artifact", "version": 3, "by": "app", "fields": {"judgement": {"enum": ["ready", "not-ready"]}, "questions": {"list": {"n": "number", "text": "text", "recommendation": "text"}}, "impl": {"enum": ["routine", "novel"]}, "files": {"list": "text"}, "steps": {"list": {"title": "text", "paths": {"list": "text"}, "report": "text"}}, "rests_on": {"list": "U[0-9]+"}}}
trigger: {"state": "plan"}
ceilings: {"turns": 40, "usd": 4.0}
---
Orders the work and names its proof, and writes no code.
