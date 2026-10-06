---
# Reads, and only reads, as plan does; plan's ceilings, not measured.
name: "Kenaz"
glyph: "ᚲ"
description: "torch: knowledge and craft"
model: {"id": "claude-opus-5-5[1m]", "effort": "medium"}
skills: ["write-spec"]
tools: {"Read": "allow", "Glob": "allow", "Grep": "allow"}
input: {"artifacts": ["intent", "spike?", "spec?"], "outputs": [], "answers": true, "findings": false, "data": ["mentions"]}
output: {"kind": "artifact", "version": 2, "by": "app", "fields": {"judgement": {"enum": ["ready", "not-ready"]}, "questions": {"list": {"n": "number", "text": "text", "recommendation": "text"}}, "unmeasured": {"list": "U[0-9]+"}}}
trigger: {"state": "spec"}
ceilings: {"turns": 40, "usd": 4.0}
---
Decides the requirements and the shape of the solution, and writes no code and no plan.
