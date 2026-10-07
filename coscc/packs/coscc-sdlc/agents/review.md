---
# Reads, and only reads: the app writes review.md. 40/$4 chosen: 20/$2 stopped rounds.
name: "Tiwaz"
glyph: "ᛏ"
description: "Reviews the pull request and says whether it may merge."
model: {"id": "claude-opus-5-5[1m]", "effort": "medium"}
variants: {"novel": {"model": {"id": "claude-opus-5-5[1m]", "effort": "high"}}}
skills: ["write-review"]
tools: {"Read": "allow", "Glob": "allow", "Grep": "allow", "codegraph": "allow"}
input: {"artifacts": ["intent", "spec?", "plan?", "impl", "pr?"], "outputs": ["plan?"], "answers": true, "findings": true, "data": ["integration", "screens", "mentions"]}
output: {"kind": "review", "version": 2, "by": "app", "fields": {"verdict": {"enum": ["pass", "changes-requested", "needs-person"]}, "criteria": {"list": {"criterion": "[RPOS][0-9]+", "source": "text", "met": {"enum": ["yes", "no", "unclear"]}, "evidence": "text"}}, "findings": {"list": {"id": "F[0-9]+", "state": {"enum": ["open", "fixed", "needs-person", "claim-rejected", "answered"]}, "fixed_in": "([0-9a-f]{7,40})?", "severity": {"enum": ["high", "medium", "low"]}, "criterion": "[RPOS][0-9]+", "path": "text", "lines": "text", "text": "text"}}, "screens": {"list": {"path": ".*\\.png", "size": "[0-9]+x[0-9]+", "address": "text", "result": "text"}}}}
ceilings: {"turns": 40, "usd": 4.0}
---
Judges the change and never edits code.
