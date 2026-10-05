---
paths:
  - "ui/src/**"
  - "ui/index.html"
  - "coscc/http/auth.py"
  - "coscc/features/*/*.js"
  - "coscc/features/*/page.py"
---

# The UI standard

What a screen may show. The `paths:` globs above are the files that count as screens (the
studio in `ui/`, the login page, a feature's script and page): the loop reads them to tell a UI
unit from any other.
A feature's page lives in `coscc/features/<name>/page.py`.

## What a good screen looks like

A plain operations console, built from four parts:

- **Left navigation**: where the person is and where else they can go, the same on every page.
- **Table with filters**: the many things of one kind, narrowed without leaving the page.
- **Detail drawer**: one row opened beside the table, holding what the table leaves out.
- **Status chip**: a row's state in one word and one colour, read at a glance.

## Rules

**S1. Short text, one sentence per place.** A label, message or empty state says one thing in
one sentence.

**S2. No lists of limits and risks on the screen.** They belong in documentation, not in front
of the person using the tool. A sentence stays only beside a button that spends money or acts
on a shared resource.

**S3. No internal detail unless the person opens it.** Environment variable names, full SHAs,
paths, epochs and UUIDs stay behind a detail view. A violation: `COS_WORKING_DIR` in a hint.

**S4. Times are shown for a reader.** Relative or a short local date, never a raw timestamp.

**S5. A list is shown as a list or a table,** never joined into prose.

**S6. The app's own text is English.** An artifact's content (Vietnamese prose under `.cos/`)
is data and does not count.

**S7. Do not ask for a name once there is a one-person login.** The app writes the fixed word
`owner`, which says someone held the password, not who. A violation: a "Your name" field beside
an action.

**S8. A disabled button says why, or is hidden.** The reason is in view; a tooltip alone does not
count on a phone.

**S9. A screen answers three questions at a glance:** what is this screen for, what needs
attention, and what is the next step. Plain labels and a calm layout, the same across screens,
are how it answers them; a screen that needs the documentation to answer one fails.

## How it is checked

A screen is judged from its screenshots, opened states included, never inferred from code or
tests.

The gate reads the words of the review's `### Screens` section, never the images. A finding
whose first word is an `S<n>` always blocks, even rated `low`. The app retakes screenshots
before `review` when the head was rewritten after them.

## What each stage does on a UI unit

A UI unit changes a file under `paths:`.

- `spec` lists in `## Design` each screen (at most six) as an app address with the `S<n>` rules
  that apply, on the fixture workspace and units the repository provides; each opened state it
  changes (a drawer, a dialog) is an address of its own within the six; it says when a screen or
  state has no address.
- `impl`, with a clean tree after its last UI commit, captures the spec's addresses, reads every
  PNG against S1-S9, fixes, commits and captures again, so the manifest's `head` is the last UI
  commit. `## Screens` records the command, its exit code, the `head`, each image path and the
  reason for every manifest `hit` left; an unexplained hit is a `high` finding.
- `review` reads `.screens/manifest.json` and every PNG in it, and adds `### Screens`: first
  line exactly `Taken at: <manifest head>. Standard: .claude/rules/ui-standard.md. Looked at by:
  <agent session>, from screenshots.`, then `- <path>.png — <W>×<H> — <address> — <what you
  saw>` per image; `<what you saw>` answers S9's three questions in a few words, and a missing
  answer is an `S9` finding. A violation is a finding whose first word after its severity is the
  rule id.
  `high` and `changes-requested`: no manifest or image, a `head` older than the last UI commit,
  `dirty: true`, an unexplained `hits` entry. When the prompt says the app took the screenshots
  again, a hit counts as explained if `impl.md ## Screens` explains one with the same address,
  size and kind. If the manifest `head` is not an ancestor of HEAD, capture its addresses first.
  Unreachable screens and opened states with no address go under `### What was not reviewed`.
