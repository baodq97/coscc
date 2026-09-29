---
name: incident
description: Record a manual intervention or a harness stumble as an incident, and investigate the incident list by root cause. Use when a person had to step in by hand, when a step failed, stalled or cost far more than it should, or when asked to review, group or fix incidents.
---

# Record and investigate incidents

Every manual intervention is an incident. Record it with its evidence; do not fix it on the
spot as its own unit. When the list is large enough, group it by root cause and fix the root.

## Where

The workspace's incident list, one row per incident, groups at the top:

```markdown
| Date | What | Evidence | Group |
|---|---|---|---|
| <YYYY-MM-DD> | <what was observed, and what the person did by hand> | <path:line, run id, cost, time> | <G<n> or —> |
```

## Record

- What happened, not why: the observed behaviour, what the person did, what it cost.
- Numbers, not impressions: turns, $, seconds, files, run ids.
- A row that repeats an earlier one still gets its own row: the count is the evidence.

## Investigate

When asked, or when ten rows have no group:

1. Group by root cause, not by symptom: the one mechanism whose change removes every row in
   the group. A row can name only one group.
2. For each group, one entry:

   ```markdown
   - **G<n>. <root cause>** (<rows>): <the rows, briefly>. Root: <the mechanism, with
     path:line>. Fix: <the change at the root>. Status: open | intent <unit> | resolved <sha>.
   ```

3. Prefer a fix that removes code, a rule or a route over one that adds one; say what it removes.
4. Rank the groups by rows × cost. The top one becomes an intent through `write-intent`, with
   the originator.
5. When its fix ships, mark the group `resolved <sha>`; a row after that reopens it.

## Done when

Every row has a group or a reason it has none, and each group names one root and one fix.
