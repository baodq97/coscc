---
# Reads, and only reads: it checks the idea against the code. spec's ceilings, not measured.
name: "Nauthiz"
glyph: "ᚾ"
description: "need"
model: {"id": "claude-opus-5-5[1m]", "effort": "medium"}
skills: ["write-intent"]
tools: {"Read": "allow", "Glob": "allow", "Grep": "allow"}
input: {"artifacts": ["idea?", "intent?"], "outputs": [], "answers": true, "findings": false, "data": ["idea", "mentions"]}
output: {"kind": "artifact", "version": 3, "by": "app", "fields": {"judgement": {"enum": ["ready", "not-ready"]}, "questions": {"list": {"n": "number", "text": "text", "recommendation": "text"}}, "type": {"enum": ["feat", "fix", "docs", "refactor", "test", "chore", "perf", "build", "ci", "revert"]}, "fix?": {"reproduction": "text", "expected": {"source": "[^\\s:]+(:[0-9]+-[0-9]+)?", "text": "text"}, "actual": "text"}}}
trigger: {"state": "intent"}
ceilings: {"turns": 40, "usd": 4.0}
---
States the problem and the outcome that would prove it solved, and decides no design.
