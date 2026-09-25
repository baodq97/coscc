---
paths:
  - "coscc/screens.py"
  - "coscc/studio.py"
  - "coscc/ui.py"
  - "coscc/state.py"
  - "coscc/coscc.py"
  - "coscc/auth.py"
  - "coscc/service.py"
  - "coscc/backlog.py"
  - "coscc/updater.py"
  - "coscc/update.py"
  - "coscc/events.py"
---

# The UI standard

What a screen of this app may show, and how. It is read by the `impl` that changes a screen
and by the `review` that looks at one; `write-spec`, `write-impl` and `write-review` point
here by path and rule id and never restate a rule. The `paths:` list above is the whole list
of files that count as the app's screens: Claude Code loads this file when a session touches
one of them, and `.claude/scripts/cos.mjs` reads the same list to tell a UI unit from any
other. There is no second copy of it.

Style references: GitHub and Linear. Where a rule below leaves a choice open, do what they
do.

These rules come from the words of `.cos/0083_*/intent.md ## Answers, câu 2`, not from
screenshots: the five screenshots that answer mentions were never committed (spec C5).

## Rules

**S1. Short text, one sentence per place.** A label, a message or an empty state says one
thing in one sentence.
A violation looks like: a card or banner carrying a paragraph, or two sentences where one
would do.

**S2. No lists of limits and risks on the screen.** Those belong in documentation
(`.claude/CLAUDE.md`, the rules files), not in front of the person using the tool.
A violation looks like: a panel explaining what the feature does not protect against, who
else could press the button, or which proof has not been run.

**S3. No internal detail unless the person opens it.** Environment variable names, full
SHAs, file system paths, epochs and UUIDs stay hidden until the person expands a detail
view or asks for it.
A violation looks like: `COS_WORKING_DIR` in a hint, a 40-character SHA in a card, a
`/home/...` or `/tmp/...` path in a header, `1759000000` where a time should be.

**S4. Times are shown for a reader.** Relative ("3 min ago") or a short local date and
time, never a raw timestamp.
A violation looks like: `2026-09-25T04:13:29Z` or an epoch in visible text.

**S5. A list is shown as a list or a table.** Several items of the same kind are never
joined into a run of prose.
A violation looks like: "F1, F2 and F4 are open; F3 was fixed in abc1234 and F5 …" in one
paragraph where a list of findings belongs.

**S6. The app's own text is English.** Labels, buttons and messages the app writes are in
English. The content of an artifact shown on the screen (the Vietnamese prose under `.cos/`)
is data, not the app's text, and does not count (`.cos/0083_*/spec.md ## Answers, câu 1`).
A violation looks like: a button reading "Đăng xuất" or "Áp dụng ngay", or an English
screen with a Vietnamese error message.

**S7. Do not ask for a name once there is a one-person login.** The session already says
who is there.
A violation looks like: a "Your name" field beside *Send this answer*, *Stop* or *Pause*.
Many recorded fields are typed names today (`Answered by:`, `stopped_by`, the backlog's
`by`); the first unit that removes such a field decides what name replaces it, and the
originator chooses it (spec C4).

**S8. A disabled button says why, or is hidden.** A control that cannot be used either
carries its reason in view (a tooltip alone does not count on a phone) or is not shown.
A violation looks like: a greyed *Run* with no sentence saying what it waits for.

## How it is checked

- **Who takes the screenshots.** The `impl` of a unit that changes a file listed above,
  with `uv run python scripts/capture_screens.py <address>...`, after its last commit that
  touches such a file. It writes PNGs and a `manifest.json` into `.screens/`, which git
  ignores.
- **Who looks.** The `review` agent, by opening each PNG with `Read`. It is an agent looking
  at screenshots, not a person, and its `### Screens` section says so.
- **What the gate reads.** The `ship` gate reads the words of the last passing round's
  `### Screens` in `review.md` — its header line, its `.png` lines, and whether `Taken at`
  is still current — never the images. A finding whose text after the severity opens with
  `S<n>` always blocks, even when rated `low`.
