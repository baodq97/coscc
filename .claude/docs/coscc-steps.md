# Board steps

Read this before changing the timeline, stop, the running list or a step's ending.

- Reply and transcript tails of a failed step are kept in the database and reach the next
  prompt and the board for whoever holds the password. A tool's output can carry a token or a
  local path.
- One step per unit, any number of units at once; a second request for a unit is refused before
  it spends. The running list is in memory, so a restart forgets it and a second copy of the app
  is not seen.
- Stop closes the CLI and cancels the task, then signals descendants through an SDK-private
  attribute: an SDK that renames it loses this silently. The step ends `stopped`, writes no
  artifact, and keeps whatever it committed or pushed. A step already writing its artifact
  refuses it.
- A repair or closing turn (an exhausted review, a prose reply without its opening) runs past
  `max_budget_usd`, because the CLI compares cost only after the turn; Stop is refused
  meanwhile. Its cost is recorded apart from the step's.
