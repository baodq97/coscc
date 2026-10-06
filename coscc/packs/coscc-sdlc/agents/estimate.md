---
# The backlog's Propose estimates button: one turn, no tools. Ceilings chosen, not measured.
name: "Estimate"
model: {"id": "claude-opus-5-5[1m]", "effort": "medium"}
tools: {}
output: {"kind": "session", "version": 1, "purpose": "Hand the app your estimate of every backlog unit, with the relations you propose.", "fields": {"units": {"list": {"unit": "text", "value": "number", "effort": "text", "similar": {"list": "text"}, "basis": "text", "relations": {"list": {"type": "text", "other": "text", "reason": "text"}}}}}}
trigger: {"engine": "estimate"}
ceilings: {"turns": 1, "usd": 2.0}
warning: "Proposing estimates opens one paid session (1 turn, $2.00 ceiling) on the model of the Agents page row `estimate`. Whoever holds the password or a live session can press it, and can rewrite any estimate, relation or the shortlist under any name they type."
---
