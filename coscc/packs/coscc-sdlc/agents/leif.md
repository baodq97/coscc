---
# The chat. Its tools are the machine's own (COS_TOOLS), not this row's. Six turns: a tool call
# takes one, and the reply needs another after it.
name: "Leif"
tools: {}
output: {"kind": "reply"}
trigger: {"engine": "chat"}
ceilings: {"turns": 6}
---
