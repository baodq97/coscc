#!/usr/bin/env python3
"""Proof for the store's `0025_rerunning-a-stage-erases-what-was-added-to-it`.

The plan's nine claims, C1-C9: re-running any of the five prose stages (`idea`, `intent`,
`spec`, `plan`, `review`) whose artifact already carries a `## Answers` section leaves that
section on disk byte for byte, whatever the reply says — even a reply that carries no
`## Answers` at all, even one that forges its own, even `review.md` picking up a new round
in the same run, and even a reply the runner refuses outright.

    0  every claim held
    1  at least one did not
    2  the environment could not answer: no `node` or no `uv`

**No session, no quota, no network.** `coscc.runner.Runner` is run for real, five times over
one fixture unit, but the session behind it is a fake that returns a fixed reply and nothing
else — the same shape `coscc/runner_test.py`'s `ReviewRoundsAccumulate.Replies` uses.
Everything lives under one temporary directory, set as both `COS_DATA_DIR` and
`COS_WORKING_DIR` before `coscc` is imported, so `~/.cos` is never opened. `node` is used
only to call `.claude/scripts/cos.mjs`'s own `parseAnswers` and `parseReview` on what ended
up on disk (C7) — the same functions every gate reads through.

**This does not measure a person answering through the app.** `scripts/verify_0016.py`
already measures that a real answer, given through `POST /api/units/answer`, reaches disk
and the next stage's prompt. This measures the other half: that the answer survives the
*next* stage running.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, say  # noqa: E402

STAGES = ["idea", "intent", "spec", "plan", "impl", "pr", "review", "ship"]
UNIT = "0001_proof"

IDEA_MARK = "IDEA-ANSWER-MARK-7c1a"
INTENT_MARK = "INTENT-ANSWER-MARK-7c1a"
SPEC_MARK = "SPEC-ANSWER-MARK-7c1a"
PLAN_MARK = "PLAN-ANSWER-MARK-7c1a"
REVIEW_MARK = "REVIEW-ANSWER-MARK-7c1a"
FORGED_MARK = "FORGED-BY-THE-REPLY-7c1a"


def require_environment() -> None:
    for tool in ("node", "uv"):
        if shutil.which(tool) is None:
            print(f"no {tool} on PATH — this proof cannot answer without it")
            raise SystemExit(EXIT_ENV)


# --- an independent reading of "the Answers section", so the claims do not lean on the
# module they are about to check. Same rule as `coscc/service.py:900` and
# `.claude/scripts/cos.mjs:194`: the line, trailing whitespace stripped, is exactly
# "## Answers". Byte-based throughout, on purpose (`plan.md` Risk 3).


def answers_bytes(raw: bytes) -> bytes | None:
    idx = 0
    while True:
        nl = raw.find(b"\n", idx)
        line = raw[idx: nl if nl != -1 else len(raw)]
        if line.rstrip(b" \t\r") == b"## Answers":
            return raw[idx:]
        if nl == -1:
            return None
        idx = nl + 1


def fixture_prose(title: str, marker: str) -> bytes:
    text = (
        f"# {title}: rerunning a stage erases what was added to it (proof fixture)\n"
        f"Author: verify_0025. Status: accepted.\n\n"
        f"## Problem\n\nA fixture this proof invented, not a real artifact.\n\n"
        f"## Open questions\n\n1. Placeholder — already answered below?\n\n"
        f"## Answers\n\n"
        f"### Câu 1\n"
        f"Answered by: verify_0025. Date: 2026-09-24. Via: product.\n\n"
        f"{marker}: giữ nguyên khối này qua một lần chạy lại.\n"
    )
    return text.encode("utf-8")


def fixture_plan(marker: str) -> bytes:
    """The one fixture whose Answers block ends `\\r\\n` and carries a non-ASCII
    character — `plan.md` Risk 3: on Linux a byte-changing bug in the write path would
    never show up on a plain `\\n`, ASCII-only fixture."""
    text = (
        "# Plan: rerunning a stage erases what was added to it (proof fixture)\n"
        "Author: verify_0025. Status: accepted.\n\n"
        "## Files that change\n\nNone — this is a fixture, not a real plan.\n\n"
        "## Open questions\n\n1. Placeholder — already answered below?\n\n"
        "## Answers\n\n"
        "### Câu 1\r\n"
        "Answered by: verify_0025. Date: 2026-09-24. Via: product.\r\n"
        "\r\n"
        f"{marker}: giữ khối này qua một lần chạy lại — dấu tiếng Việt và CRLF: "
        "đã duyệt kế hoạch “ệ”.\r\n"
    )
    return text.encode("utf-8")


def fixture_review(marker: str) -> bytes:
    text = (
        "# Review: rerunning a stage erases what was added to it (proof fixture)\n"
        "PR: https://example.invalid/proof/pull/1. Status: changes-requested.\n\n"
        "## Round 1\n\n"
        "Reviewed: abc1234000000000000000000000000000000ab. Verdict: changes-requested.\n\n"
        "### Findings\n\n"
        f"- F1 [open] fixture.py:1 — high — {marker}-ROUND1\n\n"
        "## Answers\n\n"
        "### Câu 1\n"
        "Answered by: verify_0025. Date: 2026-09-24. Via: product.\n\n"
        f"{marker}: giữ nguyên khối này qua một lần chạy lại.\n"
    )
    return text.encode("utf-8")


def reply_prose(title: str) -> str:
    return (
        f"# {title}: rerunning a stage erases what was added to it (proof fixture)\n"
        f"Author: verify_0025. Status: accepted.\n\n"
        "## Problem\n\nThe reply this proof sends back, deliberately with no "
        "## Answers section at all — the reply a model gives when it does not copy "
        "the section back.\n"
    )


def reply_review() -> str:
    return (
        "# Review: rerunning a stage erases what was added to it (proof fixture)\n"
        "PR: https://example.invalid/proof/pull/1. Status: accepted.\n\n"
        "## Round 2\n\n"
        "Reviewed: def5678000000000000000000000000000000cd. Verdict: pass.\n\n"
        "### Findings\n\nnone\n"
    )


def forged_spec_reply() -> str:
    return (
        "# Spec: rerunning a stage erases what was added to it (proof fixture)\n"
        "Author: verify_0025. Status: accepted.\n\n"
        "## Requirements\n\nA second pass, still with nothing of its own to answer.\n\n"
        "## Answers\n\n### Câu 1\nAnswered by: a-model-pretending-to-be-a-person. "
        f"Date: 2099-01-01. Via: product.\n\n{FORGED_MARK}\n"
    )


def bad_spec_reply() -> str:
    return "# Spec: a reply with no Status line\n\nThis reply carries no Status field.\n"


def bad_review_reply() -> str:
    """Rewrites round 1 instead of only adding a new one — the refusal `a063fd5` built,
    which this unit's fix must not weaken (`plan.md` Risk 1)."""
    return (
        "# Review: rerunning a stage erases what was added to it (proof fixture)\n"
        "Status: accepted.\n\n"
        "## Round 1\n\n"
        "Reviewed: abc1234000000000000000000000000000000ab. Verdict: changes-requested.\n\n"
        "### Findings\n\n- F1 [open] fixture.py:1 — high — TAMPERED-ROUND-ONE\n\n"
        "## Round 3\n\nReviewed: 999999900000000000000000000000000000ff. Verdict: pass.\n\n"
        "### Findings\n\nnone\n"
    )


class FixedReply:
    """A session that always says the same thing, however many times it is asked."""

    def __init__(self, text: str) -> None:
        self.text = text

    async def stream(self, cwd, prompt, session_id=None, max_turns=1, **kw):
        yield ("chunk", self.text)
        yield ("done", {"session_id": "verify-0025", "cost": {}})


async def run_stage(root: Path, directory: Path, stage: str, artifact: str, reply: str) -> dict:
    from coscc.runner import Runner

    runner = Runner(FixedReply(reply), None)
    last = None
    async for item in runner.run(
        workspace=str(root), directory=directory, journal_key=str(root), unit=UNIT,
        stage=stage, artifact=artifact, stages=STAGES, mode="manual",
    ):
        last = item
    return last[1]


NODE_PROBE = """
// Reads a JSON array of records and reports, for each, how many `## Answers` blocks
// `parseAnswers` reads before and after a run, and — for the review record — how many
// rounds `parseReview` reads. Same functions every gate in cos.mjs reads through.
import { readFileSync, writeFileSync } from 'node:fs'

const [, , harnessPath, inPath, outPath] = process.argv
const { parseAnswers, parseReview } = await import(harnessPath)
const records = JSON.parse(readFileSync(inPath, 'utf-8'))
const out = records.map((r) => ({
  stage: r.stage,
  before: parseAnswers(r.before).length,
  after: parseAnswers(r.after).length,
  rounds: r.isReview ? parseReview(r.after).rounds.length : null,
}))
writeFileSync(outPath, JSON.stringify(out))
"""


def node_answer_counts(root: Path, records: list[dict]) -> list[dict]:
    from coscc import harness

    probe = root / "probe.mjs"
    probe.write_text(NODE_PROBE, encoding="utf-8")
    in_path = root / "probe-in.json"
    out_path = root / "probe-out.json"
    in_path.write_text(json.dumps(records), encoding="utf-8")
    done = subprocess.run(
        ["node", str(probe), str(harness.script()), str(in_path), str(out_path)],
        capture_output=True, text=True, timeout=60,
    )
    if done.returncode != 0:
        raise RuntimeError(f"the node probe failed: {done.stderr}")
    return json.loads(out_path.read_text(encoding="utf-8"))


async def run(root: Path) -> bool:
    directory = root / ".cos" / UNIT
    directory.mkdir(parents=True)

    fixtures = {
        "idea": ("idea.md", fixture_prose("Idea", IDEA_MARK), reply_prose("Idea")),
        "intent": ("intent.md", fixture_prose("Intent", INTENT_MARK), reply_prose("Intent")),
        "spec": ("spec.md", fixture_prose("Spec", SPEC_MARK), reply_prose("Spec")),
        "plan": ("plan.md", fixture_plan(PLAN_MARK), reply_prose("Plan")),
        "review": ("review.md", fixture_review(REVIEW_MARK), reply_review()),
    }
    for stage, (artifact, content, _reply) in fixtures.items():
        (directory / artifact).write_bytes(content)

    ok = True
    claim_labels = {"idea": "C1", "intent": "C2", "spec": "C3", "plan": "C4", "review": "C5"}
    before_sections: dict[str, bytes] = {}
    after_texts: dict[str, str] = {}
    before_texts: dict[str, str] = {}

    for stage in ("idea", "intent", "spec", "plan", "review"):
        artifact, content, reply = fixtures[stage]
        path = directory / artifact
        before_bytes = path.read_bytes()
        before_section = answers_bytes(before_bytes)
        assert before_section is not None, f"fixture bug: {artifact} has no ## Answers"
        before_sections[stage] = before_section
        before_texts[stage] = before_bytes.decode("utf-8")

        done = await run_stage(root, directory, stage, artifact, reply)
        after_bytes = path.read_bytes()
        after_texts[stage] = after_bytes.decode("utf-8", "replace")

        label = claim_labels[stage]
        ok &= say(
            done["outcome"] == "done" and after_bytes.endswith(before_section),
            f"{label} re-running {stage} with a reply carrying no ## Answers keeps it, "
            "byte for byte",
            f"outcome={done['outcome']!r}, error={done.get('error')!r}, "
            f"kept={after_bytes.endswith(before_section)}",
        )

    # C6: review.md's new round lands before ## Answers, and round 1 is still there.
    review_after = (directory / "review.md").read_bytes()
    i_round2 = review_after.find(b"## Round 2")
    i_answers = review_after.find(b"## Answers")
    i_round1_marker = review_after.find(f"{REVIEW_MARK}-ROUND1".encode())
    ok &= say(
        i_round2 != -1 and i_answers != -1 and i_round1_marker != -1
        and i_round1_marker < i_round2 < i_answers,
        "C6 review.md's new round sits before ## Answers, and round 1 is still there",
        f"round1@{i_round1_marker}, round2@{i_round2}, answers@{i_answers}",
    )

    # C7: cos.mjs's own readers see the same number of Answers blocks after as before,
    # on all five files, and two rounds on review.md.
    records = [
        {"stage": s, "before": before_texts[s], "after": after_texts[s], "isReview": s == "review"}
        for s in ("idea", "intent", "spec", "plan", "review")
    ]
    counts = node_answer_counts(root, records)
    by_stage = {c["stage"]: c for c in counts}
    ok &= say(
        all(by_stage[s]["before"] == by_stage[s]["after"] for s in by_stage)
        and by_stage["review"]["rounds"] == 2,
        "C7 cos.mjs's parseAnswers counts the same blocks after as before on all five "
        "files, and parseReview sees 2 rounds on review.md",
        json.dumps(counts),
    )

    # C8 (R3): re-running spec with a reply that forges its own ## Answers. The forged
    # block must not reach disk, and the section already there must not move a byte.
    spec_path = directory / "spec.md"
    spec_section_before_c8 = answers_bytes(spec_path.read_bytes())
    done = await run_stage(root, directory, "spec", "spec.md", forged_spec_reply())
    spec_after_c8 = spec_path.read_bytes()
    spec_section_after_c8 = answers_bytes(spec_after_c8)
    ok &= say(
        done["outcome"] == "done"
        and FORGED_MARK.encode() not in spec_after_c8
        and spec_section_after_c8 == spec_section_before_c8,
        "C8 a reply that forges its own ## Answers does not reach disk, and the real "
        "section does not move a byte",
        f"outcome={done['outcome']!r}, forged present={FORGED_MARK.encode() in spec_after_c8}, "
        f"section unchanged={spec_section_after_c8 == spec_section_before_c8}",
    )

    # C9 (R5): a refused reply changes no byte, for the two stages this proof can drive —
    # spec (no Status: line) and review (a reply that rewrites round 1).
    review_path = directory / "review.md"
    spec_sha_before = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    review_sha_before = hashlib.sha256(review_path.read_bytes()).hexdigest()

    spec_done = await run_stage(root, directory, "spec", "spec.md", bad_spec_reply())
    review_done = await run_stage(root, directory, "review", "review.md", bad_review_reply())

    spec_sha_after = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    review_sha_after = hashlib.sha256(review_path.read_bytes()).hexdigest()
    ok &= say(
        spec_done["outcome"] != "done" and spec_sha_before == spec_sha_after
        and review_done["outcome"] != "done" and review_sha_before == review_sha_after,
        "C9 a refused reply (no Status: line; a rewritten review round) changes no byte "
        "of the artifact on disk",
        f"spec outcome={spec_done['outcome']!r} sha kept={spec_sha_before == spec_sha_after}; "
        f"review outcome={review_done['outcome']!r} sha kept={review_sha_before == review_sha_after}",
    )

    return bool(ok)


def main() -> int:
    require_environment()
    with tempfile.TemporaryDirectory(prefix="verify-0025-") as d:
        root = Path(d)
        # Before `coscc` is imported: some of its modules build state from the
        # environment at import time, and that must open this directory, not `~/.cos`.
        os.environ["COS_DATA_DIR"] = str(root / "data")
        os.environ["COS_WORKING_DIR"] = str(root / "work")
        print(f"temporary data root: {root}")
        return EXIT_PASS if asyncio.run(run(root)) else EXIT_BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
