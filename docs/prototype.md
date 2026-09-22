# COS Studio: interactive design preview

Open `/prototype` after `uv run cos-build` and `uv run cos-baodo`. This is a design
prototype, not a replacement for the existing app at `/`, and not a shipped backend
integration. The user has not yet approved the design.

**Implementation status:** built and verified locally on 2026-09-22, including the five
demo flows in Chromium, light/dark, responsive layout and the original application's
browser checks. The initial execution-permission blocker was resolved when the user
requested build and run. This does not constitute design approval or permission to ship.

## Product direction

The workspace is the persistent context. Navigation answers where you are; the board
answers what needs to happen next. Work detail opens alongside that context rather than
replacing it. Human decisions and AI activity are distinct, and an agent's accepted
artifact is never presented as a human approval.

The visual language uses neutral surfaces, iris accents, semantic status colors,
restrained borders, system typography and progressively disclosed detail. It uses the
existing Radix/Reflex theme, without remote fonts, images, handwritten HTML or CSS files.
`studio.py` contains the presentation primitives; `prototype_data.py` contains invented
typed examples; `prototype.py` owns preview state and screen composition.

## Screens and interactions

| Area | What to try |
|---|---|
| Overview | Workspace-scoped summary, continue a work item, jump into conversations |
| Workspaces | Search, create, rename, select, and confirm removal of demo workspaces |
| Board | Search, mode/attention filters, board/list views, create a demo work item |
| Work detail | Read the sample artifact and timeline, change demo mode, run a simulation |
| Sessions | Select or create conversations, send messages, switch back to retained history |
| Activity & Usage | Read sample events and usage derived from the selected workspace's work |
| Settings | Light/dark, board density, read-only environment and policy explanations |

The shell search opens screens and work items. On narrow screens navigation moves into
a dialog. Empty, loading and error states can be selected in the preview bar and exited
without changing data. Keyboard users can navigate controls and close dialogs with Escape.

## Deliberate limits

- Data is invented and held only in Reflex preview state. It is not durable; a reload or
  new client session may restore the fixtures. Theme uses Reflex's existing browser
  preference, shared with the original app.
- Replies and live runs are scripted. No model, tools, real tokens, files or business
  API operations are involved. The usage figures are labeled sample values, not estimates
  of this session's bill.
- Board lanes organize attention; they do not implement or override lifecycle gates.
  The sample complete item is illustrative, not evidence that this prototype shipped.
- Environment/policy cards explain the intended baseline, not a live audit of the host.
  Working-root and tool-permission settings cannot be changed here.
- Auth, invitations, billing, backend integration, drag-to-bypass-gate behavior, and
  deployment are deliberately absent rather than represented by nonfunctional controls.
- `write-impl`, `write-review` and `write-ship` still need a separately scoped correction:
  recording that work happened is not the same as doing it. Ship is not authorized.

## Verification

```sh
npm test
uv run cos-build
uv run python scripts/verify_0009.py
```

The browser proof starts the regular loopback app against a temporary working root, then
checks the five agreed flows, six screens, preview states, light/dark, and document
overflow at 390, 768, 1024 and 1440 CSS pixels. It also verifies board/summary column counts
and gutters, and checks that the prototype sends no business API requests.
Use `--screenshots /absolute/output/directory` for visual evidence.

Exit 0 means checks passed; a broken interaction fails with exit 1; missing build/browser
or an occupied app port is exit 2. The proof never starts real AI sessions. Appearance
still needs direct user review; passing interaction checks is not design approval.
