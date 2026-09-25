# Settings and the backlog

Read this before changing `POST /api/settings/*`, `coscc/models.py`, `POST /api/backlog/*` or `coscc/backlog.py`. Moved here whole from `.claude/rules/coscc-app.md` (`0094`); the history ("Since `00xx`") is kept at this tier.

- **`POST /api/settings/models` decides what every step spends, for whoever holds the
  password.** Since
  the store's `0004_no-setting-says-which-model-runs-a-stage` each stage, and chat, runs
  on the model Settings names: an override in the `prefs` table (`model:<name>`), else
  `coscc/models.json`, else `COS_MODEL`. Anyone holding the password can move `review` to
  a weak model or every stage to a dear one, `0.0.0.0` by default. The trace is a
  `setting` record in the run log (workspace `""`, with `old` and `new` — it shows on no
  workspace's Activity) and the `override` badge on Settings. A model id is not checked
  when saved; a wrong one fails the stage's next step with the CLI's error. A person who
  had `COS_MODEL` set before this lost it for every stage: it now answers only chat.
  Since `0033` every row also has an effort (`effort:<name>`, `POST /api/settings/efforts`,
  the same `setting` trace), and each stage after `plan` has a `<stage>:novel` row used when
  the plan's label is `novel`: declared, forced by a file in `coscc/labels.py`
  `SECURITY_SURFACE`, missing (every plan written before `0033`), or escalated because an
  earlier `impl` of the unit stopped at `max_turns`. So a routine `impl` that runs out of
  turns reruns on the dearer row with nobody pressing anything different. Since `0062`
  that rerun, and every `impl` labelled `novel` — a `missing` plan written before `0033`
  included — also gets 250 turns / $16.0 instead of 120 / $8.0 (`policy.NOVEL_CEILINGS`,
  shown on Settings as `impl:novel`), so one press can spend twice as much. `max` is refused
  from `models.json` and taken from an override, so anyone holding the password can set it.
  The password is what stands in front; `COS_HOST=127.0.0.1` still narrows who can try it.
- **`POST /api/backlog/*` writes the backlog's order, and `propose` opens a paid session, for
  whoever holds the password.** Since `0074`. `estimate`, `relation` and `shortlist` each
  append one run-log row (`estimate-value`, `relation`, `shortlist`) under a typed `by`; a
  person's name may not start with `agent:`, but a hand-edited row in `cos.db` can, and the
  board then takes it for an agent's (`plan.md` Risk 9). `propose` opens one session on the
  model of the Settings row `estimate` — no tools, 1 turn, $2.0, all chosen, and nobody has
  measured a prompt carrying ~70 units — and writes `start`/`end` rows with `unit: ""` and
  `stage: "estimate"`, one `estimate` row, and each valid part of the reply. A second press in
  the same workspace is refused, in this process only (`_active`). *Apply* waits for it
  like an integration. No gate, no `next` and no run button reads any of it; every board
  step's `start` row carries `shortlist` (R14), read only by `verify_0074 --measure`. A step
  started at a terminal has none, so the outcome's measurement cannot see it (`spec.md ##
  Answers, câu 3`). The password is what stands in front; `COS_HOST=127.0.0.1` still narrows
  who can try it.
