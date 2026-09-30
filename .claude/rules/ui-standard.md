---
paths:
  - "coscc/screens/__init__.py"
  - "coscc/screens/studio.py"
  - "coscc/ui.py"
  - "coscc/state/__init__.py"
  - "coscc/coscc.py"
  - "coscc/auth.py"
  - "coscc/service/__init__.py"
  - "coscc/units/backlog.py"
  - "coscc/update/updater.py"
  - "coscc/update/__init__.py"
  - "coscc/runlog/events.py"
  - "coscc/screens/backlog.py"
  - "coscc/screens/board.py"
  - "coscc/screens/chrome.py"
  - "coscc/screens/common.py"
  - "coscc/screens/dialogs.py"
  - "coscc/screens/idea.py"
  - "coscc/state/ideas.py"
  - "coscc/state/place.py"
  - "coscc/state/present.py"
  - "coscc/service/ideas.py"
  - "coscc/screens/overview.py"
  - "coscc/screens/sessions.py"
  - "coscc/screens/settings.py"
  - "coscc/screens/unit.py"
  - "coscc/service/activity.py"
  - "coscc/service/agents.py"
  - "coscc/service/answers.py"
  - "coscc/service/autopilot.py"
  - "coscc/service/backlog.py"
  - "coscc/service/board.py"
  - "coscc/service/common.py"
  - "coscc/service/models.py"
  - "coscc/features/*.py"
  - "coscc/service/sessions.py"
  - "coscc/service/steps.py"
  - "coscc/service/update.py"
  - "coscc/service/watch.py"
  - "coscc/service/workspaces.py"
  - "coscc/state/answers.py"
  - "coscc/state/app.py"
  - "coscc/state/backlog.py"
  - "coscc/state/rerun.py"
  - "coscc/state/release.py"
  - "coscc/service/release.py"
  - "coscc/state/update.py"
  - "coscc/state/views.py"
  - "coscc/state/watch.py"
  - "coscc/state/workspaces.py"
  - "coscc/service/resume.py"
---

# The UI standard

What a screen of this app may show. The `paths:` list above is the whole list of files that count
as screens: `.claude/scripts/cos.mjs` reads it to tell a UI unit from any other. Style
references: GitHub and Linear.

## Rules

**S1. Short text, one sentence per place.** A label, a message or an empty state says one
thing in one sentence.
A violation looks like: a card or banner carrying a paragraph, or two sentences where one
would do.

**S2. No lists of limits and risks on the screen.** Those belong in documentation
(the rules files), not in front of the person using the tool.
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
is data, not the app's text, and does not count.
A violation looks like: a button reading "Đăng xuất" or "Áp dụng ngay", or an English
screen with a Vietnamese error message.

**S7. Do not ask for a name once there is a one-person login.** The session already says
who is there.
A violation looks like: a "Your name" field beside *Send this answer*, *Stop* or *Pause*.
The app writes the fixed word `owner` into those fields (`Answered by:`, `stopped_by`, the
backlog's, hold's and update's `by`) when a request names nobody. `owner` is not an identity: it says
someone held the password or a live session, not who.

**S8. A disabled button says why, or is hidden.** A control that cannot be used either
carries its reason in view (a tooltip alone does not count on a phone) or is not shown.
A violation looks like: a greyed *Run* with no sentence saying what it waits for.

## How it is checked

- `impl` of a unit that changes a listed file runs
  `uv run python scripts/capture_screens.py <address>...` after its last such commit; it writes
  PNGs and `manifest.json` into `.screens/` (git-ignored). The app retakes them before `review`
  when the head was rewritten after.
- `review` opens each PNG with `Read`; its `### Screens` section says an agent looked.
- The `ship` gate reads the round's `screens` object (path, size, address, result, and whether
  `taken` is current), never the images. A finding whose `rule` is an `S<n>` always blocks, even
  rated `low`.

## What each stage does on a UI unit

A UI unit changes a file listed under `paths:`.

- `spec` lists in `## Design` each screen (at most six) as an app address, with the `S<n>` rules
  that apply, on fixture workspace `proj` and its fixture units; it says when a screen has no
  address.
- `impl`, with a clean tree after its last UI commit, captures the spec's addresses (at most
  six), reads every PNG against `S1`-`S8`, fixes, commits and captures again, so the manifest's
  `head` is the last UI commit. `## Screens` records the command, its exit code, the `head`,
  each image path, and the reason for every manifest `hit` left; an unexplained hit is a `high`
  finding. If the last line says the `.web` rebuild failed, run the command it prints before
  any browser proof.
- `review` reads `.screens/manifest.json` and every PNG in it, and adds `### Screens`: first
  line exactly `Taken at: <manifest head>. Standard: .claude/rules/ui-standard.md. Looked at by:
  <agent session>, from screenshots.`, then `- <path>.png — <W>×<H> — <address> — <what you
  saw>` per image. A violation is a finding whose first word after its severity is the rule id.
  `high` and `changes-requested`: no manifest or image, a `head` older than the last UI commit,
  `dirty: true`, an unexplained `hits` entry. When the prompt says the app took the screenshots
  again, a hit counts as explained if `impl.md ## Screens` explains one with the same address,
  size and kind. If the manifest `head` is not an ancestor of HEAD, capture its addresses first.
  Screens not reachable go under `### What was not reviewed`.
