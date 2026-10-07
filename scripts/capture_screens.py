#!/usr/bin/env python3
"""Screenshots of the app as built from this checkout, for a unit that changes a screen.

The `impl` of a unit
that changes a file `.claude/rules/ui-standard.md` lists runs this after its last commit
touching such a file; the `review` of that unit opens every PNG it wrote with `Read`.

    uv run python scripts/capture_screens.py <address>... [--out <dir>]

Each address is a path of the app, starting with `/`, at most six of them. Each is taken
at 1440×900 and 390×844 (or `--sizes`), full page, into `<out>/<address slug>-<W>x<H>.png` (`<out>`
defaults to `.screens/` in this checkout, which git ignores). The PNGs and manifest of the
last run there are removed first and nothing else; a `<out>` that holds files but no
`manifest.json` is refused, since this command did not write it. So is a tree with
uncommitted changes: the screens must be `head`'s. Beside
them `<out>/manifest.json` records `head` (this checkout's `HEAD`, 40 hex), `dirty` (the
tree changed while it ran), the time, the addresses, the sizes, every shot, and `hits`: every place the visible text of a
page matched one of the six patterns of the UI standard that can be measured (`scan`).
A hit is reported, never an exit code.

The app runs on a temporary data root with one workspace, `proj`, a clone of a bare
directory, and five units in it, always the same, so a spec can name its addresses (the studio's paths are
`/`, `/up-next`, `/work/proj`, `/unit/proj/2`, ...; `ui/src/routes.tsx` lists them):

    0001_fresh-intent      an accepted intent, nothing else; it walks the `short` process, the rest `full`
    0002_open-question     an intent with two open questions nobody answered, the first with a
                           recommendation (*Take it* on `/inbox/proj/2`), the second without
    0003_awaiting-ship     every artifact up to a passing review round; the PR machine's row
                           names github.com/o/r/pull/1
    0004_finished          shipped (a merge-read row), plan.md accepted with a plan record of two
                           parallel steps (`seed_transitions`); three questions on its intent: one
                           Leif answered (`delegated`, so `/decisions` lists it), one you answered
                           (`person`), one open; a rerun, a round allowed and an outcome you
                           recorded, so its Activity shows them;
                           one `plan` run of it, ended, in the run log, so `/`, `/activity` and its Timeline show a time;
                           shipped nine days ago and its outcome graded (one criterion met, one not,
                           one unclear, a proposal), so its Outcome block and `/insights` show it
    0005_unfinished-review every artifact up to a review whose round 2 asked for
                           changes and left out F1 of round 1; the PR machine's row names
                           github.com/o/r/pull/2

After them `make_idea_fixture` makes `0006_frontend-calls-api`, and
`0007_unread-status` has a spec with no state (a status no stage writes is no transition). Then `AUTOPILOT_FIXTURE`:

    0008_draft-impl        a draft impl.md with no question, gone on with twice on
                           one head by the autopilot (`make_autopilot_fixture`)
    0009_refused-impl      an accepted plan, whose `impl` the autopilot queued and
                           the gate refused with `gate-closed` (`seed_refusal`)
    0010_paused-impl       an accepted plan whose `impl` hit its $4 ceiling, was raised to $8
                           and hit that: held `budget-reached`, two parts of one session

Every file is written by hand as prose; each unit's states (the `statuses`, `type`, `shipped`
and `questions` its fixture carries) are seeded as rows in `cos.db` by `seed_fixture`, since
the board reads a unit from there and never from its files.

The run log holds a `spec` run that ended `done` for $0.52 and an `impl` run
that ended `failed` after 109 turns with no known cost on `0002_open-question`, and an
`impl` run on `0004_finished` that ended `failed` with neither; also two
`impl` runs and one `integrate` opened by a conflict on `0002_open-question` ($16.32 in all,
over the $15 budget, one `impl` at four times the median tokens per turn) and three `impl`
runs on `0004_finished`, then a last `impl` run that failed, and an `estimate` and a `chat` run
of no unit, each with its events like the `integrate` one (`seed_runs`), so every anomaly
`/cost` knows has a row and `/agents` shows `impl` as `failed` and `spec` as `ok` (an `idle`
agent has no run: `idea`), and one chat conversation in a temporary `CLAUDE_CONFIG_DIR`, titled `Backlog screen
plan`, whose reply is markdown (`seed_conversation`). Beside each PNG it writes the page's
visible text as `<address slug>-<W>x<H>.txt`.

`intent` is edited on the Agents page (Haiku, low effort, `Grep` off, `Glob` on ask, a line
added to its role) and `spike` has a hand-written owner file its checks refuse (`seed_agents`).

`codegraph` is at `pilot` in `proj` (`seed_pilot`), so `/settings` shows its pilot sentence;
only with `npm` on `PATH`, else the feature is locked and the row shows `off`.

`proj` holds a `pyproject.toml` at `0.1.0` tagged
`v0.1.0`, then a `feat` and a `build(deps)` commit of no unit (`seed_release`), so `/work/proj`
shows its *Release* panel ready with `0.2.0` proposed.

The autopilot is on for `proj` and its shortlist names `0008` and `0009` alone, so `/up-next`
shows both on its shortlist, and it starts nothing.
**Add a unit to the shortlist, or let one of the two leave its stop, and the app under the
camera starts real steps**, sessions that spend quota.

For example `/`, `/up-next`, `/work/proj` or `/unit/proj/2`.
An address ending in `#<id>` of a closed part (`<details>`) is taken with that part open.
The fixture's paths live under `/tmp/`, so a screen that shows the workspace's path today
hits the standard on every run; say so rather than hide it.

A page is taken full length (the viewport is grown to the page's scroll height), except one
with a dialog open: it is taken as the viewport shows it, and what the dialog holds below its
fold is not in the image (`full_page` in the manifest says which).

It logs in by writing a password hash and one session into that root before the app
starts, and puts a `gh` first on `PATH` that answers `pr list` with `[]` and refuses the
rest. No session, no quota, no network.

The studio (`coscc/_studio/`) is built first when it is missing or older than `ui/src`; a
failed build is exit 2.

    0  every address taken at both sizes
    1  a page did not open: the login page, or no `#studio-shell` in time
    2  the environment is not ready — too many addresses, one not starting with `/`, an
       `<out>` this command did not write, uncommitted changes, no studio build, no browser
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent

import httpx

from coscc.config import COOKIE
from scripts.proof_harness import (
    EXIT_BROKEN,
    EXIT_ENV,
    EXIT_PASS,
    RealApp,
    ensure_studio,
    seed_fixture,
    make_repo,
    require_browser,
    seed_session,
)

SIZES = ((1440, 900), (390, 844))  # the two sizes measured before
MAX_ADDRESSES = 6  # 6 × 2 sizes = 12 images, the ceiling (chosen, not measured)
PAGE_TIMEOUT_MS = 20_000
MAX_HEIGHT = 12_000  # a taller page is cut here (chosen)
SETTLE_MS = 1_500  # for the page's reads to fill it after `#studio-shell` shows
OPEN_MS = 300  # for a closed part opened by the address to lay out (chosen, not measured)

# What the standard forbids that a pattern can find in visible text.
PATTERNS = (
    ("sha", re.compile(r"\b[0-9a-f]{40}\b")),
    (
        "uuid",
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
    ),
    ("epoch", re.compile(r"\b(?:\d{13}|\d{10})\b")),
    ("env", re.compile(r"\b(?:COS|COSCC)_[A-Z0-9_]+")),
    ("path", re.compile(r"(?:/home/|/tmp/|~/)\S+")),
    ("iso-time", re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")),
)

INTENT = "# Intent: {title}\nAuthor: capture_screens. Type: feat. Status: accepted.\n\n## Problem\n\n{problem}\n"
ROUND = "\n## Round 1\n\nReviewed: {sha}. Verdict: pass.\n\n### Findings\n\n### What was not reviewed\n\nNothing.\n"
ASKED = "\n## Round {n}\n\nReviewed: {sha}. Verdict: changes-requested.\n\n### Findings\n\n{findings}\n\n### What was not reviewed\n\nNothing.\n"
# What the fixture's review rounds graded: one met, one not, one unclear.
CRITERIA = [
    {
        "criterion": "R1",
        "source": "Một unit thiếu test thì không xong",
        "met": "no",
        "evidence": "a.py:1",
    },
    {
        "criterion": "R2",
        "source": "Tên gọi nói được việc của nó",
        "met": "unclear",
        "evidence": "b.py:2",
    },
    {
        "criterion": "S3",
        "source": "Không lộ chi tiết nội bộ",
        "met": "yes",
        "evidence": "ui/src/screens/UnitPage.tsx:40",
    },
]
FIXTURE = {
    "fresh-intent": {
        "statuses": {"intent.md": "accepted"},
        "type": "feat",
        "files": {
            "intent.md": INTENT.format(
                title="fresh intent", problem="Một intent vừa được chấp nhận."
            )
        },
    },
    # Two open questions, so the Questions tab's dialog shows both above its fold at 1440×900.
    "open-question": {
        "statuses": {"intent.md": "accepted"},
        "type": "feat",
        "questions": {
            "intent.md": [
                (
                    "Nhánh lấy tên từ đâu?",
                    "Từ `unit-branch`: loại của intent và slug, để tên luôn đúng ngữ pháp.",
                ),
                "Có nên trả thêm tiền cho việc này không?",
            ]
        },
        "files": {
            "intent.md": INTENT.format(title="open question", problem="Một intent còn một câu hỏi.")
            + "\n## Open questions\n\n1. Nhánh lấy tên từ đâu?\n"
            + "2. Có nên trả thêm tiền cho việc này không?\n",
        },
    },
    "awaiting-ship": {
        "statuses": dict.fromkeys(
            ("intent.md", "spec.md", "plan.md", "impl.md", "pr.md", "review.md"), "accepted"
        ),
        "type": "feat",
        "files": {
            "intent.md": INTENT.format(
                title="awaiting ship", problem="Một unit đã qua review, chờ ship."
            ),
            "spec.md": "# Spec: awaiting ship\nIntent: intent.md. Author: capture_screens.\n",
            "plan.md": "# Plan: awaiting ship\nAuthor: capture_screens.\n",
            "impl.md": "# Impl: awaiting ship\nAuthor: capture_screens.\n",
            "pr.md": "# PR: awaiting ship\nPR: https://github.com/o/r/pull/1. Author: capture_screens.\n",
            "review.md": "# Review: awaiting ship\nAuthor: capture_screens.\n"
            + ROUND.format(sha="a" * 40),
        },
        "pr": 1,
        "rounds": [(1, "a" * 40, "pass", [], [{**CRITERIA[0], "met": "yes"}, CRITERIA[2]])],
    },
    # One question Leif answered and one you answered, so `/decisions` and the Activity tab show
    # both kinds; one nobody answered, so the Questions tab has something to show read-only. A
    # rerun, a round allowed and an outcome, each a decision row the Activity tab lists.
    "finished": {
        "statuses": dict.fromkeys(("intent.md", "spec.md", "plan.md"), "accepted"),
        "type": "feat",
        "shipped": True,
        "questions": {
            "intent.md": [
                ("Có cần đo lại sau một tuần không?", "Có: đo lại sau 7 ngày, cùng một truy vấn."),
                "Ai đọc kết quả?",
                "Ai ký duyệt kết quả?",
            ]
        },
        "answers": [
            (
                "intent.md",
                1,
                "Có: đo lại sau 7 ngày, cùng một truy vấn.",
                "delegated",
                "Leif (CoS)",
                "2026-09-18",
            ),
            ("intent.md", 3, "Tôi ký.", "person", "owner", "2026-09-19"),
        ],  # fmt: skip
        "decisions": [
            ("rerun", {"stage": "spec", "stale": {}}, "2026-09-17"),
            ("more-rounds", {"rounds": 1}, "2026-09-19"),
            (
                "outcome",
                {
                    "result": "met",
                    "measured_by": "agent",
                    "source": "cos.db",
                    "reason": "",
                    "note": "",
                },
                "2026-09-21",
            ),
        ],  # fmt: skip
        "files": {
            "intent.md": INTENT.format(title="finished", problem="Một unit đã xong.")
            + "\n## Proposed outcome\n\nĐến hết ngày 2026-09-20, việc này đã được đo.\n"
            + "\n## Open questions\n\n1. Có cần đo lại sau một tuần không?\n2. Ai đọc kết quả?\n"
            + "3. Ai ký duyệt kết quả?\n",
            "spec.md": "# Spec: finished\nAuthor: capture_screens.\n",
            "plan.md": "# Plan: finished\nAuthor: capture_screens.\n",
        },
    },
    # A last round that does not count, so the unit's dialog lists the id
    # it left out.
    "unfinished-review": {
        "statuses": {
            **dict.fromkeys(("intent.md", "spec.md", "plan.md", "impl.md", "pr.md"), "accepted"),
            "review.md": "changes-requested",
        },
        "type": "feat",
        "files": {
            "intent.md": INTENT.format(
                title="unfinished review", problem="Một vòng review bỏ sót một finding."
            ),
            "spec.md": "# Spec: unfinished review\nIntent: intent.md. Author: capture_screens.\n",
            "plan.md": "# Plan: unfinished review\nAuthor: capture_screens.\n",
            "impl.md": "# Impl: unfinished review\nAuthor: capture_screens.\n",
            "pr.md": "# PR: unfinished review\nPR: https://github.com/o/r/pull/2. Author: capture_screens.\n",
            "review.md": "# Review: unfinished review\nAuthor: capture_screens.\n"
            + ASKED.format(
                n=1,
                sha="b" * 40,
                findings="- F1 [open] a.py:1 — medium — Thiếu test.\n- F2 [open] b.py:2 — low — Tên chưa rõ.",
            )
            + ASKED.format(n=2, sha="c" * 40, findings="- F2 [open] b.py:2 — low — Tên chưa rõ."),
        },
        "pr": 2,
        "rounds": [
            (
                1,
                "b" * 40,
                "changes-requested",
                [
                    {
                        "id": "F1",
                        "state": "open",
                        "fixed_in": "",
                        "severity": "medium",
                        "criterion": "R1",
                        "path": "a.py",
                        "lines": "1",
                        "text": "Thiếu test.",
                    },
                    {
                        "id": "F2",
                        "state": "open",
                        "fixed_in": "",
                        "severity": "low",
                        "criterion": "R2",
                        "path": "b.py",
                        "lines": "2",
                        "text": "Tên chưa rõ.",
                    },
                ],
                CRITERIA,
            ),
            (
                2,
                "c" * 40,
                "changes-requested",
                [
                    {
                        "id": "F2",
                        "state": "open",
                        "fixed_in": "",
                        "severity": "low",
                        "criterion": "R2",
                        "path": "b.py",
                        "lines": "2",
                        "text": "Tên chưa rõ.",
                    },
                ],
                CRITERIA,
            ),
        ],  # fmt: skip
    },
}

# The two units the shortlist names, each at a stop of the autopilot, so it starts neither.
AUTOPILOT_FIXTURE = {
    # A draft `impl.md` with no question, gone on with twice on one head: stop `e`.
    "draft-impl": {
        "statuses": {
            **dict.fromkeys(("intent.md", "spec.md", "plan.md"), "accepted"),
            "impl.md": "draft",
        },
        "type": "feat",
        "files": {
            "intent.md": INTENT.format(title="draft impl", problem="Một impl còn draft."),
            "spec.md": "# Spec: draft impl\nIntent: intent.md. Author: capture_screens.\n",
            "plan.md": "# Plan: draft impl\nIntent: intent.md. Author: capture_screens.\n",
            "impl.md": "# Impl: draft impl\nIntent: intent.md. Author: capture_screens.\n",
        },
    },
    # An `impl` the autopilot queued that the gate refused (`seed_refusal`): stop `f`.
    "refused-impl": {
        "statuses": dict.fromkeys(("intent.md", "spec.md", "plan.md"), "accepted"),
        "type": "feat",
        "files": {
            "intent.md": INTENT.format(title="refused impl", problem="Một impl bị gate từ chối."),
            "spec.md": "# Spec: refused impl\nIntent: intent.md. Author: capture_screens.\n",
            "plan.md": "# Plan: refused impl\nIntent: intent.md. Author: capture_screens.\n",
        },
    },
    # An `impl` that hit its $ ceiling, was raised once and hit the higher one (`seed_runs`): held
    # `budget-reached`, one session in two parts.
    "paused-impl": {
        "statuses": dict.fromkeys(("intent.md", "spec.md", "plan.md"), "accepted"),
        "type": "feat",
        "files": {
            "intent.md": INTENT.format(
                title="paused impl", problem="Một impl dừng ở trần chi phí."
            ),
            "spec.md": "# Spec: paused impl\nIntent: intent.md. Author: capture_screens.\n",
            "plan.md": "# Plan: paused impl\nIntent: intent.md. Author: capture_screens.\n",
        },
    },
}
DRAFT_IMPL, REFUSED_IMPL, PAUSED_IMPL = "0008_draft-impl", "0009_refused-impl", "0010_paused-impl"

Rows = list[tuple[Path, str, Mapping[str, Any]]]

FAKE_GH = '#!/bin/sh\nif [ "$1" = pr ] && [ "$2" = list ]; then echo \'[]\'; exit 0; fi\nexit 1\n'


# One conversation with a title and a markdown reply, written where the SDK
# reads its sessions (no session is opened, no quota spent).
CHAT_TITLE = "Backlog screen plan"
CHAT = (
    ("user", "Can the backlog be a table?"),
    (
        "assistant",
        [
            {
                "type": "text",
                "text": "Yes. The **plan** is:\n\n- one row per unit\n- *Edit* opens in the row",
            }
        ],
    ),
)


def seed_release(proj: Path) -> None:
    """One release behind, so the board's *Release* panel is `ready`. Pushed, so
    `origin/main` and the tag are what a board read counts from."""
    from scripts.proof_harness import git

    (proj / "pyproject.toml").write_text(
        '[project]\nname = "proj"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    git(proj, "add", "-A")
    git(proj, "commit", "-q", "-m", "chore: release 0.1.0")
    git(proj, "tag", "v0.1.0")
    for subject, name in (
        ("feat: search the board (#21)", "search.txt"),
        ("build(deps): bump deps (#22)", "deps.txt"),
    ):
        (proj / name).write_text(subject + "\n", encoding="utf-8")
        git(proj, "add", "-A")
        git(proj, "commit", "-q", "-m", subject)
    git(proj, "push", "-q", "origin", "main", "v0.1.0")


def seed_conversation(workspace: Path) -> str:
    """One conversation under `CLAUDE_CONFIG_DIR` for `workspace`; returns its session id.
    `CLAUDE_CONFIG_DIR` must already be set, and the app started after this."""
    import uuid

    from claude_agent_sdk._internal import sessions as sdk

    where = sdk._get_projects_dir() / sdk._sanitize_path(sdk._canonicalize_path(str(workspace)))
    where.mkdir(parents=True, exist_ok=True)
    sid, parent, lines = str(uuid.uuid4()), None, []
    for i, (kind, content) in enumerate(CHAT):
        me = str(uuid.uuid4())
        lines.append(
            json.dumps(
                {
                    "type": kind,
                    "uuid": me,
                    "parentUuid": parent,
                    "sessionId": sid,
                    "cwd": str(workspace),
                    "timestamp": f"2026-09-24T01:00:0{i}.000Z",
                    "isSidechain": False,
                    "userType": "external",
                    "message": {"role": kind, "content": content},
                }
            )
        )
        parent = me
    lines.append(json.dumps({"type": "custom-title", "customTitle": CHAT_TITLE, "sessionId": sid}))
    (where / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sid


def scan(text: str) -> list[tuple[str, str]]:
    """Every place `text` matches one of `PATTERNS`, as `(kind, snippet)`: the match with up
    to 20 characters either side, on one line."""
    hits = []
    for kind, pattern in PATTERNS:
        for m in pattern.finditer(text):
            around = text[max(0, m.start() - 20) : m.end() + 20]
            hits.append((kind, " ".join(around.split())))
    return hits


def slug(address: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "-", address).strip("-") or "root"


def git_out(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True
    ).stdout


def with_env(**values: str) -> dict[str, str]:
    return {**os.environ, **values}


def make_fixture(
    api: httpx.Client, proj: Path, rows: Rows, fixture: Mapping[str, Mapping[str, Any]] = FIXTURE
) -> None:
    """The units of `fixture`, numbered in this order after those already there (0001–0005
    for `FIXTURE`), through the app's own route. Each goes onto `rows` with its states, for
    `seed_fixture`."""
    for name, unit in fixture.items():
        files = unit["files"]
        made = api.post(
            "/api/units",
            json={
                "cwd": str(proj),
                "slug": name,
                "brief": f"The {name.replace('-', ' ')} fixture.",
            },
        )
        if made.status_code != 200:
            raise RuntimeError(f"could not make the unit {name}: {made.text}")
        for file, text in files.items():
            (Path(made.json()["path"]) / file).write_text(text, encoding="utf-8")
        states = {k: v for k, v in unit.items() if k != "files"}
        rows.append((proj, made.json()["unit"], states))


def make_all(api: httpx.Client, work: Path, data_dir: Path, proj: Path, other: Path) -> None:
    """Every unit of the capture, then each one's states as rows."""
    rows: Rows = []
    make_fixture(api, proj, rows)
    make_idea_fixture(api, proj, other, rows)
    make_unread_fixture(api, proj, rows)
    make_autopilot_fixture(api, work, data_dir, proj, rows)
    seed_fixture(work, data_dir, rows)
    from coscc.store.db import Data

    with Data(data_dir).write() as conn:
        conn.execute(
            "UPDATE unit_meta SET process = 'coscc-sdlc/short' WHERE unit = '0001_fresh-intent'"
        )


def make_idea_fixture(api: httpx.Client, proj: Path, other: Path, rows: Rows) -> None:
    """`proj/ideas/0001_one-feature.md`, `api/0001_backend-adds-api` opened from it (its `idea` row), and
    `proj/0006_frontend-calls-api`, whose `impl` waits on the api unit: it has no `ship.md`."""
    idea = api.post(
        "/api/ideas",
        json={
            "cwd": str(proj),
            "slug": "one-feature",
            "brief": "The backend adds an API; the frontend calls it.",
        },
    )
    if idea.status_code != 200:
        raise RuntimeError(f"could not make the idea: {idea.text}")
    ref = idea.json()["ref"]
    back = api.post("/api/units", json={"cwd": str(other), "slug": "backend-adds-api", "idea": ref})
    if back.status_code != 200:
        raise RuntimeError(f"could not open the api unit: {back.text}")
    back_ref = f"api/{back.json()['unit']}"
    Path(back.json()["path"], "intent.md").write_text(
        "# Intent: backend adds api\nAuthor: the originator.\n", encoding="utf-8"
    )
    rows.append(
        (other, back.json()["unit"], {"statuses": {"intent.md": "accepted"}, "type": "feat"})
    )
    front = api.post(
        "/api/units",
        json={"cwd": str(proj), "slug": "frontend-calls-api", "idea": ref, "depends_on": back_ref},
    )
    if front.status_code != 200:
        raise RuntimeError(f"could not open the frontend unit: {front.text}")
    for file, text in {
        "intent.md": "# Intent: frontend calls api\nAuthor: the originator.\n",
        "spec.md": "# Spec: frontend calls api\nIntent: intent.md. Author: t.\n",
        "plan.md": "# Plan: frontend calls api\nIntent: intent.md. Author: t.\n",
    }.items():
        Path(front.json()["path"], file).write_text(text, encoding="utf-8")
    states = dict.fromkeys(("intent.md", "spec.md", "plan.md"), "accepted")
    rows.append((proj, front.json()["unit"], {"statuses": states, "type": "feat"}))


def make_unread_fixture(api: httpx.Client, proj: Path, rows: Rows) -> None:
    """`proj/0007_unread-status`, whose `spec.md` has no state (an accepted intent and a spec
    with no row). Made after `make_idea_fixture`, so the numbers a spec names stay where
    they were."""
    made = api.post(
        "/api/units",
        json={"cwd": str(proj), "slug": "unread-status", "brief": "The unread status fixture."},
    )
    if made.status_code != 200:
        raise RuntimeError(f"could not make the unit unread-status: {made.text}")
    for file, text in {
        "intent.md": INTENT.format(
            title="unread status", problem="Một spec mang status không stage nào viết."
        ),
        "spec.md": "# Spec: unread status\nIntent: intent.md. Author: capture_screens.\n",
    }.items():
        (Path(made.json()["path"]) / file).write_text(text, encoding="utf-8")
    rows.append(
        (proj, made.json()["unit"], {"statuses": {"intent.md": "accepted"}, "type": "feat"})
    )


def seed_agents(api: httpx.Client, data_dir: Path) -> None:
    """`intent` edited on the Agents page: Haiku at low effort, `Grep` off and `Glob` on ask, a
    line added to its role; and a hand-written owner file for `spike` that its checks refuse, so
    `/agents` shows an edited row, an `ask` tool and an agent that cannot run."""
    intent = next(r for r in api.get("/api/agents").json()["rows"] if r["key"] == "intent")
    tools = {**intent["row"]["tools"], "Glob": "ask"}
    tools.pop("Grep", None)
    for field, value in (
        ("model", {"id": "claude-haiku-4-5", "effort": "low"}),
        ("tools", tools),
        ("body", intent["row"]["body"] + "\nBegin your reply with the word MARKER-M4."),
    ):
        said = api.post("/api/agents/field", json={"key": "intent", "field": field, "value": value})
        if said.status_code != 200:
            raise RuntimeError(f"could not edit intent's {field}: {said.text}")
    bad = data_dir / "packs" / "local" / "agents" / "spike.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text('---\nceilings: {"turns": 0, "usd": 4.0}\n---\n', encoding="utf-8")


def seed_run(work: Path, data_dir: Path, proj: Path) -> None:
    """One ended `plan` run of `0004_finished` in the running app's run log, keyed
    as `Workspaces.key` keys it, with the envelope its `start` names. `at` is when it is written, so the page reads "just now"."""
    import uuid

    from coscc.store.journal import Journal

    journal, key = Journal(work, data_dir), str(proj.resolve())
    journal.started(
        key, "0004_finished", "plan", "manual", envelope=["intent.md", "spec.md", "answers"]
    )
    journal.finished(key, "0004_finished", "plan", "done", session_id=str(uuid.uuid4()))


def seed_transitions(work: Path, data_dir: Path, proj: Path) -> None:
    """Rows a guard decided, so the Timeline tab shows guard labels and whose
    decision each was. Each sets the state its artifact already holds, so no lane moves:
    `0003_awaiting-ship`'s round and a CI read at its head, `0004_finished`'s plan result."""
    from coscc.units.history import History

    history, key = History(work, data_dir), str(proj.resolve())
    head = "a" * 40
    history.record(
        key,
        "0003_awaiting-ship",
        "pr.md",
        "accepted",
        guard="ci-at-head",
        authority="code",
        run="capture-pr-reader",
        inputs={"number": 1, "head": head, "read_head": head, "ci": "green"},
        actor="app",
        source="capture_screens",
    )
    history.record(
        key,
        "0003_awaiting-ship",
        "review.md",
        "accepted",
        guard="review-round",
        authority="agent",
        run="capture-review-1",
        inputs={"head": head, "verdict": "pass"},
        actor="capture_screens",
        source="capture_screens",
    )
    history.record(
        key,
        "0004_finished",
        "plan.md",
        "accepted",
        guard="stage-result",
        authority="agent",
        run="capture-plan-1",
        inputs={"judgement": "ready"},
        actor="capture_screens",
        source="capture_screens",
    )
    # The record that result handed back, so the unit page's *Outputs* tab shows a plan's
    # label, files and parallel steps.
    from coscc.units.meta import UnitMeta

    meta = UnitMeta(work, data_dir)
    plan = {
        "stage": "plan",
        "judgement": "ready",
        "questions": [],
        "variant": "routine",
        "files": ["coscc/board.py", "tests/test_board.py", "ui/src/Board.tsx"],
        "steps": [
            {"title": "The board reads the rows", "paths": ["coscc/board.py",
             "tests/test_board.py"], "report": "the board's tests, green"},
            {"title": "The screen", "paths": ["ui/src/Board.tsx"], "report": "a screenshot"},
        ],
        "rests_on": [],
    }  # fmt: skip
    with meta.data.write() as conn:
        meta.record_result(
            conn, key, "0004_finished", "plan", "plan.md", {"run": "capture-plan-1", "object": plan}
        )


def seed_runs(work: Path, data_dir: Path, proj: Path) -> None:
    """A run whose cost is unknown on two units and one whose
    cost is known, written where the app reads its run log, under the key it reads by; and
    `codegraph` at `pilot` (`seed_pilot`)."""
    from coscc.store.db import Data
    from coscc.store.journal import Journal

    seed_pilot(data_dir, proj)
    journal, key = Journal(work, Data(data_dir)), str(proj.resolve())
    journal.started(key, "0002_open-question", "spec", "manual")
    journal.finished(key, "0002_open-question", "spec", "done", turns=4, cost_usd=0.52)
    journal.started(key, "0002_open-question", "impl", "autonomous", run="capture-run-1")
    journal.finished(
        key,
        "0002_open-question",
        "impl",
        "failed",
        run="capture-run-1",
        turns=109,
        cost_unknown=True,
        detail="ProcessError: Command failed with exit code -9",
    )
    # Enough that each of the four anomalies has a row on
    # `/cost` — `0002` over $15, `impl` run too often on both units, one `impl` step at four
    # times the median tokens per turn — and an `integrate` opened by a conflict.
    for turns, usd, tokens in ((20, 6.00, 800_000), (30, 9.00, 300_000)):
        journal.started(key, "0002_open-question", "impl", "autonomous")
        journal.finished(
            key, "0002_open-question", "impl", "done", turns=turns, cost_usd=usd, **_tokens(tokens)
        )
    journal.started(
        key,
        "0002_open-question",
        "integrate",
        "manual",
        integrate_state="conflicting",
        agent="Gebo",
        run="capture-integrate-1",
    )
    _run_events(work, data_dir, key, "0002_open-question", "integrate", "capture-integrate-1")
    journal.finished(
        key,
        "0002_open-question",
        "integrate",
        "done",
        agent="integrate",
        status="done",
        run="capture-integrate-1",
        turns=6,
        cost_usd=0.80,
    )
    # Runs no unit holds, each with its events: Insights lists the estimate and chat beside the
    # stages, and `/run/proj/capture-estimate-1` opens one.
    for stage, run, usd in (
        ("estimate", "capture-estimate-1", 0.11),
        ("chat", "capture-chat-1", 0.04),
    ):
        journal.started(key, "", stage, "manual", run=run)
        _run_events(work, data_dir, key, "", stage, run)
        journal.finished(
            key, "", stage, "done", agent=stage, status="done", run=run, turns=2, cost_usd=usd
        )
    # One `impl` that paused at $4.00, was raised to $8.00 and paused again: two runs, one session.
    journal.started(key, PAUSED_IMPL, "impl", "manual", run="capture-paused-1")
    _run_events(work, data_dir, key, PAUSED_IMPL, "impl", "capture-paused-1", "paused-budget")
    journal.finished(
        key,
        PAUSED_IMPL,
        "impl",
        "paused-budget",
        run="capture-paused-1",
        agent="impl",
        status="paused-budget",
        session_id="capture-session",
        ceiling="usd",
        max_budget_usd=4.0,
        max_turns=250,
        turns=61,
        cost_usd=4.0,
        detail="stopped at the ceiling: error_max_budget_usd",
    )
    journal.raised(
        key,
        PAUSED_IMPL,
        "impl",
        run="capture-paused-2",
        by="owner",
        from_usd=4.0,
        from_turns=250,
        max_budget_usd=8.0,
        max_turns=250,
    )
    _run_events(work, data_dir, key, PAUSED_IMPL, "impl", "capture-paused-2", "paused-budget")
    journal.finished(
        key,
        PAUSED_IMPL,
        "impl",
        "paused-budget",
        run="capture-paused-2",
        agent="impl",
        status="paused-budget",
        session_id="capture-session",
        ceiling="usd",
        max_budget_usd=8.0,
        max_turns=250,
        turns=118,
        cost_usd=8.0,
        detail="stopped at the ceiling: error_max_budget_usd",
    )
    # An impl run with its events, `/run/proj/capture-impl-1`: what it was granted, and a push
    # its grant did not hold.
    journal.started(key, "0004_finished", "impl", "autonomous", run="capture-impl-1")
    _run_events(work, data_dir, key, "0004_finished", "impl", "capture-impl-1")
    journal.finished(
        key, "0004_finished", "impl", "done", agent="impl", status="done", run="capture-impl-1",
        turns=10, cost_usd=0.40, **_tokens(100_000),
    )  # fmt: skip
    for _ in range(3):
        journal.started(key, "0004_finished", "impl", "autonomous")
        journal.finished(
            key, "0004_finished", "impl", "done", turns=10, cost_usd=0.40, **_tokens(100_000)
        )
    # Two secrets on `/feature/vault`, kept by name with no value: one impl may use, one spike may.
    from coscc.vault import Store

    store = Store(Data(data_dir), config_home=str(data_dir / "cfg"), home=str(data_dir / "home"))
    store.create("ws:db", key, "the staging database", stages=("impl",))
    store.create("ws:deploy-key", key, "pushes the preview build", stages=("spike",))
    # The last `impl` run of all is this failed one, so `/agents` shows `impl` as `failed`
    # (a stage's chip is its last run's); `spec` ended `done` above and is `ok`.
    journal.started(key, "0004_finished", "impl", "autonomous")
    journal.finished(key, "0004_finished", "impl", "failed", cost_unknown=True)


def _run_events(
    work: Path, data_dir: Path, key: str, unit: str, stage: str, run: str, outcome: str = "done"
) -> None:
    """A short run's events as the recorder stores them: its config with what it was granted, a
    read, a refusal naming the grant it lacked, its words and its end."""
    from coscc.agent import policy
    from coscc.store.db import Data

    data, at = Data(data_dir), int(time.time() * 1000)
    data.step_run_open(run, str(Path(work).resolve()), key, unit, stage, at)
    if stage == "impl":
        granted = ["write: worktree, unit folder, scratch", "push: feat/finished", "helpers: scout, worker", "submit", "vault: ws:db", "codegraph"]  # fmt: skip
        refusal = ("Bash", {"command": "git push origin main"}, f"{policy.HOST}: a push may only name `origin feat/finished`", "push")  # fmt: skip
    else:
        granted = ["submit"] if stage != "chat" else []
        refusal = ("Write", {"file_path": "notes.md"}, f"{policy.WRITES}: this session has no place to write", "write")  # fmt: skip
    tool, tool_input, reason, lacked = refusal
    said = [
        ("config", {"model": "claude-opus-5-5[1m]", "effort": "medium", "granted": granted}),
        ("tool_use", {"id": "t1", "name": "Read", "input": {"file_path": "README.md"}}),
        ("denied", {"tool": tool, "input": tool_input, "reason": reason, "lacked": lacked}),
        ("text", {"text": "Three units are ready to estimate; 0002 waits on its question."}),
        ("result", {"num_turns": 2, "cost_usd": 0.11, "terminal_reason": "completed"}),
        ("end", {"outcome": outcome, "detail": ""}),
    ]
    data.step_events_add(
        run,
        [
            {"run": run, "seq": n, "at": at + n, "kind": kind, **fields}
            for n, (kind, fields) in enumerate(said, start=1)
        ],
    )
    data.step_run_close(run, at + len(said), 0)


def seed_refusal(data_dir: Path, proj: Path) -> None:
    """The autopilot's `impl` of `0009_refused-impl`, refused by the gate with `gate-closed`.
    Written before the app starts, so its scheduler never sees the row `queued`."""
    from coscc.bus import Bus
    from coscc.runner.queue import Attempts

    attempts = Attempts(data_dir, Bus())
    row = attempts.open("step", str(proj.resolve()), REFUSED_IMPL, "impl", started_by="autopilot")
    attempts.move(row["id"], "refused", "gate-closed")


SCAN_PROPOSALS = (
    (
        "fix",
        "impl-drafts-need-a-blind-rerun",
        "Impl that ends as a draft says nothing about why",
        "Twice this week an impl step ended with its record still a draft and the board offered "
        "only a rerun. Nothing on the unit said what was left open, so the owner reran it blind "
        "and read the transcript to find the cause. Each blind rerun costs a full impl session "
        "and about an hour of waiting.",
    ),
    (
        "feat",
        "ci-red-names-no-failing-job",
        "A red CI names no failing job",
        "CI went red on three pull requests and each time the unit only said that CI was red. "
        "The owner opened GitHub to find which job failed and pasted its log into a rerun note "
        "by hand. The same head was reported red again after the fix landed, so one failure was "
        "counted twice and the second rerun was spent for nothing.",
    ),
    (
        "fix",
        "branches-fall-behind-main",
        "Pull request branches fall behind main",
        "Five integrations in two days were started by hand because a branch fell behind main "
        "while its review ran. One collided with the running review and was refused, so the "
        "owner waited and pressed it again. Each one interrupts the loop and needs a person to "
        "notice that the branch is behind before review can pass.",
    ),
    (
        "chore",
        "rerun-notes-carry-decisions",
        "Rerun notes carry decisions nobody records",
        "Spec steps converged only after the owner reran them with free-text notes that carried "
        "decisions. Those notes live only in the run log, so the next stage never reads them and "
        "the same question comes back. Four reruns in two days held a decision that should have "
        "been an answer on the unit, recorded where every later stage reads it.",
    ),
)


def make_scan_fixture(api: httpx.Client, data_dir: Path, proj: Path) -> None:
    """The scan row left off for `proj`, so nothing pays, and the four proposals of one run: two
    pending, one accepted as `0006_frontend-calls-api`, one dismissed. Written through the core's
    `proposals` table, which the app made when it started."""
    from datetime import datetime, timedelta, timezone

    from coscc.store.db import Data
    from coscc.units import proposals as table

    # Release is off until a workspace turns it on; proj shows its panel.
    r = api.post("/api/features", json={"cwd": str(proj), "name": "release", "state": "on"})
    if r.status_code != 200:
        raise RuntimeError(f"could not turn release on: {r.text}")
    key = str(proj.resolve())
    now = datetime.now(timezone.utc)
    kinds = ("impl-draft", "ci-red", "integrate", "rerun", "review-round", "refused")
    units = ("0008_draft-impl", "0002_open-question", "0004_finished", "0009_refused-impl")
    taken = [
        table.Source(
            id=f"{kinds[n % 6]}:runs:{n}",
            kind=kinds[n % 6],
            unit=units[n % 4],
            at=(now - timedelta(hours=30 - n)).isoformat(timespec="seconds"),
        )
        for n in range(12)
    ]
    items = [
        {
            "type": t,
            "slug": s,
            "title": title,
            "problem": problem,
            "sources": [i["id"] for i in taken[n * 3 : n * 3 + 3]],
        }
        for n, (t, s, title, problem) in enumerate(SCAN_PROPOSALS)
    ]
    data = Data(data_dir)
    ids = table.add(data, key, "scan", "", items, run="r", sources={i["id"]: i for i in taken})
    table.claim(data, key, ids[2], "accepted")
    table.set_made(data, key, ids[2], "0006_frontend-calls-api")
    table.claim(data, key, ids[3], "dismissed", "Already answered by the questions on each unit.")


# What the outcome grader made of `0004_finished`: one sentence met, one not, one it could not
# check, so its Outcome block shows each kind and the proposal the `no` made.
OUTCOME_CRITERIA = [
    {
        "criterion": "O1",
        "source": "Màn hình Insights hiển thị chi phí trung vị của mỗi unit đã ship.",
        "met": "yes",
        "evidence": "ui/src/screens/Insights.tsx:118-130 the cost card shows the median",
    },
    {
        "criterion": "O2",
        "source": "Mỗi unit vượt ngân sách được nêu tên kèm liên kết tới trang của nó.",
        "met": "no",
        "evidence": "ui/src/screens/Insights.tsx:131-140 names at most five, the rest are cut",
    },
    {
        "criterion": "W1",
        "source": "Chi phí trung vị giảm xuống dưới $15 trong tháng tới.",
        "met": "unclear",
        "evidence": "A figure measured on runs; the repository does not record it.",
    },
]


def make_outcome_fixture(work: Path, data_dir: Path, proj: Path) -> None:
    """`0004_finished` shipped nine days ago by the app, graded by the outcome row two days later:
    its verdict, the `end` Insights counts, and the proposal its `no` made."""
    from datetime import datetime, timedelta, timezone

    from coscc.store.db import Data
    from coscc.store.journal import SHIP_RECORD, Journal
    from coscc.units import proposals as table
    from coscc.units.meta import UnitMeta
    from coscc.units.read import grader

    found = grader()
    if found is None:
        raise RuntimeError("no row grades outcomes")
    key, unit, now = str(proj.resolve()), "0004_finished", datetime.now(timezone.utc)
    journal = Journal(work, Data(data_dir))
    at = lambda days: (now - timedelta(days=days)).isoformat(timespec="seconds")
    journal.append(
        {
            "kind": SHIP_RECORD,
            "workspace": key,
            "unit": unit,
            "stage": "ship",
            "result": "shipped",
            "at": at(9),
        }
    )
    obj = {"criteria": OUTCOME_CRITERIA}
    judgement = UnitMeta(work, Data(data_dir)).record_verdict(
        key, unit, found[0], "capture-grade", obj
    )
    items = table.of_verdict(unit, OUTCOME_CRITERIA)
    table.add(Data(data_dir), key, found[0], unit, items, run="capture-grade")
    journal.started(key, unit, found[0], "manual", agent=found[0], run="capture-grade", at=at(2))
    journal.finished(
        key,
        unit,
        found[0],
        "done",
        agent=found[0],
        run="capture-grade",
        turns=14,
        cost_usd=0.18,
        verdict=judgement,
        proposals=len(items),
        at=at(2),
    )


def seed_pilot(data_dir: Path, proj: Path) -> None:
    """`codegraph` at `pilot` for `proj`, the pref written straight: `POST /api/features` would
    install its engine (about 290 MB, over the network). The row shows `pilot` only when `npm`
    is on `PATH`; without it the feature is locked and the row shows `off`. The other features'
    states stay."""
    from coscc.store.db import Data
    from coscc.http.plugin import STATE_PREF

    data = Data(data_dir)
    states = data.pref(STATE_PREF, {})
    mine = {**states.get("codegraph", {}), str(proj.resolve()): "pilot"}
    data.set_pref(STATE_PREF, {**states, "codegraph": mine})


def make_autopilot_fixture(
    api: httpx.Client, work: Path, data_dir: Path, proj: Path, rows: Rows
) -> None:
    """`AUTOPILOT_FIXTURE`, a shortlist of the first two units alone, and two tries of
    `0008_draft-impl` on one head: each an `autopilot-pick` that went on with the draft and the
    step it began, ended. Then the scan's proposals, the rest of what the Backlog shows."""
    from coscc.store.journal import Journal

    make_fixture(api, proj, rows, AUTOPILOT_FIXTURE)
    make_scan_fixture(api, data_dir, proj)
    make_outcome_fixture(work, data_dir, proj)
    journal, key = Journal(work, data_dir), str(proj.resolve())
    for _ in range(2):
        journal.append(
            {
                "kind": "autopilot-pick",
                "workspace": key,
                "unit": DRAFT_IMPL,
                "stage": "impl",
                "continued": True,
            }
        )
        journal.started(
            key, DRAFT_IMPL, "impl", "autonomous", started_by="autopilot", head="d" * 40
        )
        journal.finished(key, DRAFT_IMPL, "impl", "done", turns=8, cost_usd=0.30)
    journal.append(
        {
            "kind": "shortlist",
            "workspace": key,
            "unit": "",
            "units": [DRAFT_IMPL, REFUSED_IMPL],
            "reason": "capture_screens",
            "by": "owner",
        }
    )


def _tokens(total: int) -> dict[str, int]:
    """`total` split across the four billed kinds, cache reads the most as in real runs."""
    return {
        "input_tokens": total // 20,
        "output_tokens": total // 20,
        "cache_read_tokens": total * 8 // 10,
        "cache_creation_tokens": total // 10,
    }


def shoot(
    browser, base: str, token: str, address: str, size: tuple[int, int], out: Path
) -> tuple[Path, str, str, bool]:
    """One address at one size: the PNG, the URL it ended on, the visible text, and whether
    the image is the full page. Raises `RuntimeError` when the page is not the app's."""
    context = browser.new_context(viewport={"width": size[0], "height": size[1]})
    context.add_cookies([{"name": COOKIE, "value": token, "url": base}])
    try:
        page = context.new_page()
        page.goto(base + address, wait_until="load", timeout=PAGE_TIMEOUT_MS)
        try:
            page.wait_for_selector("#studio-shell", timeout=PAGE_TIMEOUT_MS)
        except Exception as e:
            raise RuntimeError(
                f"{address} at {size[0]}x{size[1]}: no #studio-shell within {PAGE_TIMEOUT_MS // 1000}s, on {page.url}"
            ) from e
        if "/login" in page.url:
            raise RuntimeError(
                f"{address} at {size[0]}x{size[1]}: landed on the login page, {page.url}"
            )
        page.wait_for_timeout(SETTLE_MS)
        part = address.partition("#")[2]
        if part:
            try:
                page.wait_for_selector(f"#{part}", state="attached", timeout=PAGE_TIMEOUT_MS)
            except Exception as e:
                raise RuntimeError(
                    f"{address} at {size[0]}x{size[1]}: no #{part} within {PAGE_TIMEOUT_MS // 1000}s"
                ) from e
            page.evaluate("id => { document.getElementById(id).open = true; }", part)
            page.wait_for_timeout(OPEN_MS)
        path = out / f"{slug(address)}-{size[0]}x{size[1]}.png"
        full = page.locator("[role=dialog]").count() == 0
        if full:
            # The studio scrolls inside `.scroll`, so the page itself is one viewport tall.
            height = page.evaluate(
                "() => { const s = document.querySelector('.scroll'); "
                "return s ? Math.ceil(s.scrollHeight + s.getBoundingClientRect().top) : 0; }"
            )
            if height > size[1]:
                page.set_viewport_size({"width": size[0], "height": min(height, MAX_HEIGHT)})
                page.wait_for_timeout(OPEN_MS)
        page.screenshot(path=str(path), full_page=full)
        text = page.inner_text("body")
        # The visible text beside the image, so a proof can count what the page says.
        path.with_suffix(".txt").write_text(text, encoding="utf-8")
        return path, page.url, text, full
    finally:
        context.close()


def parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("addresses", nargs="+", metavar="address")
    p.add_argument("--out", type=Path, default=REPO / ".screens")
    p.add_argument(
        "--sizes",
        default=",".join(f"{w}x{h}" for w, h in SIZES),
        help="the sizes to take, as WxH,WxH",
    )
    args = p.parse_args(argv)
    args.sizes = tuple(tuple(int(n) for n in s.split("x")) for s in args.sizes.split(","))
    return args


def run(argv: list[str]) -> int:
    args = parse(argv)
    if len(args.addresses) > MAX_ADDRESSES:
        print(
            f"{len(args.addresses)} addresses, at most {MAX_ADDRESSES} — {MAX_ADDRESSES * len(args.sizes)} images is the ceiling",
            file=sys.stderr,
        )
        return EXIT_ENV
    bad = [a for a in args.addresses if not a.startswith("/")]
    if bad:
        print(
            f"an address is a path of the app and starts with /: {', '.join(bad)}", file=sys.stderr
        )
        return EXIT_ENV
    refused = out_refused(args.out.resolve())
    if refused:
        print(refused, file=sys.stderr)
        return EXIT_ENV
    # A screen taken from uncommitted work may not be the one the pull
    # request carries, and `head` would still name the last commit.
    dirty = git_out("status", "--porcelain").rstrip("\n")
    if dirty:
        print(
            f"the tree has uncommitted changes; commit them first, so `head` is what was taken:\n{dirty}",
            file=sys.stderr,
        )
        return EXIT_ENV

    ensure_studio()
    roots: list[Path] = []
    try:
        return capture(args, roots)
    finally:
        for d in roots:
            shutil.rmtree(d, ignore_errors=True)


def out_refused(out: Path) -> str | None:
    """Why `out` may not be written into, or `None`. Only a directory this command wrote —
    one holding `manifest.json` — or an empty or missing one is: `--out .` must not reach
    the checkout ."""
    if out.exists() and not out.is_dir():
        return f"{out} is a file, not a directory"
    if out.is_dir() and any(out.iterdir()) and not (out / "manifest.json").is_file():
        return f"{out} holds files and no manifest.json — not a directory this command wrote; name an empty one"
    return None


def clear_out(out: Path) -> None:
    """Removes what a previous run wrote — its PNGs and manifest — and nothing else."""
    out.mkdir(parents=True, exist_ok=True)
    for old in [*out.glob("*.png"), *out.glob("*.txt"), out / "manifest.json"]:
        old.unlink(missing_ok=True)


def capture(args: argparse.Namespace, roots: list[Path]) -> int:
    out = args.out.resolve()
    clear_out(out)

    playwright, browser = require_browser()
    work = Path(tempfile.mkdtemp(prefix="cos-0083-work-")).resolve()
    data_dir = Path(tempfile.mkdtemp(prefix="cos-0083-data-")).resolve()
    outside = Path(tempfile.mkdtemp(prefix="cos-0083-remote-")).resolve()
    roots += [work, data_dir, outside]
    bin_dir = outside / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH, encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    # `RealApp` hands `os.environ` to the app (`scripts/proof_harness.py`).
    os.environ["CLAUDE_CONFIG_DIR"] = str(outside / "claude")

    shots, hits = [], []
    started = time.monotonic()
    try:  # noqa: PLR1702 - still to split
        proj = make_repo(work, outside)
        seed_release(proj)
        token = seed_session(data_dir)
        seed_conversation(proj)
        seed_refusal(data_dir, proj)
        with RealApp(work, data_dir) as app:
            with httpx.Client(base_url=app.base, timeout=30, cookies={COOKIE: token}) as api:
                added = api.post("/api/workspaces", json={"name": "proj"})
                if added.status_code != 200:
                    print(f"could not adopt the workspace: {added.text}", file=sys.stderr)
                    return EXIT_BROKEN
                other = make_repo(work, outside, name="api", remote="api.git")
                added = api.post("/api/workspaces", json={"name": "api"})
                if added.status_code != 200:
                    print(f"could not adopt the second workspace: {added.text}", file=sys.stderr)
                    return EXIT_BROKEN
                try:
                    make_all(api, work, data_dir, proj, other)
                    seed_agents(api, data_dir)
                    seed_runs(work, data_dir, proj)
                    seed_transitions(work, data_dir, proj)
                except RuntimeError as e:
                    print(str(e), file=sys.stderr)
                    return EXIT_BROKEN
                # The autopilot on, and each unit of the shortlist at a stop, so it starts nothing
                # and says why.
                on = api.post(
                    "/api/settings/autopilot",
                    json={"cwd": str(proj), "name": "autopilot", "value": True},
                )
                if on.status_code != 200:
                    print(f"could not turn the autopilot on: {on.text}", file=sys.stderr)
                    return EXIT_BROKEN
            seed_run(work, data_dir, proj)
            for address in args.addresses:
                for size in args.sizes:
                    try:
                        path, url, text, full = shoot(browser, app.base, token, address, size, out)
                    except RuntimeError as e:
                        print(str(e), file=sys.stderr)
                        return EXIT_BROKEN
                    where = f"{size[0]}x{size[1]}"
                    shots.append(
                        {
                            "address": address,
                            "size": where,
                            "path": str(
                                path.relative_to(REPO) if path.is_relative_to(REPO) else path
                            ),
                            "url": url,
                            "full_page": full,
                        }
                    )
                    found = scan(text)
                    hits += [
                        {"address": address, "size": where, "kind": k, "snippet": s}
                        for k, s in found
                    ]
                    print(
                        f"{where} {address} -> {shots[-1]['path']} ({path.stat().st_size} bytes, {len(found)} hits)"
                    )
    finally:
        browser.close()
        playwright.stop()

    manifest = {
        "head": git_out("rev-parse", "HEAD").strip(),
        "dirty": bool(git_out("status", "--porcelain").strip()),
        "taken_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "addresses": args.addresses,
        "sizes": [f"{w}x{h}" for w, h in args.sizes],
        "shots": shots,
        "hits": hits,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"{len(shots)} screenshots and manifest.json in {out}, head {manifest['head'][:7]}"
        f"{' (dirty tree)' if manifest['dirty'] else ''}, {len(hits)} hits, {time.monotonic() - started:.1f}s"
    )
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
