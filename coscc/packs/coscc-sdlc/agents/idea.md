---
name: "Ingwaz"
glyph: "ᛜ"
description: "seed"
model: {"id": "claude-opus-5-5[1m]", "effort": "medium"}
skills: ["write-idea"]
tools: {}
input: {"artifacts": ["idea?"], "outputs": [], "answers": true, "findings": false, "data": ["mentions"]}
output: {"kind": "artifact", "version": 2, "by": "app", "fields": {"judgement": {"enum": ["ready", "not-ready"]}, "questions": {"list": {"n": "number", "text": "text", "recommendation": "text"}}}}
trigger: {"state": "idea"}
ceilings: {"turns": 1}
---
Records the problem in the originator's own words and proposes no solution.
