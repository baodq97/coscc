"""Jera: an agent that answers a unit's open questions from precedent, when a person asks.

`0044_open-questions-wait-for-the-originator-even-when-precedent-answers-them`. Not a stage
and unknown to `cos.mjs`: one tool-less session per press of *Ask Jera*, under the grant
`precedent` (`coscc/policy.py`). Everything it may read is in its prompt; everything it
says is in one JSON block the app reads, filters and writes. `Service.precedent` is the
one caller that writes; `scripts/verify_0044.py --measure` asks and writes nothing.

Every function here but `ask` is pure: no disk, no network, no clock.

What is decided here, and what is not:
- The store of precedent (`entries`) is the Settings text and every answer in force in the
  same workspace, less Jera's own and the asked unit's (`spec.md` R8). An answer a later
  block replaced is not precedent: it is a decision that was changed (`plan.md`, *Những gì
  cố ý không làm*).
- A verdict `answer` survives only with every citation in that store and a category outside
  the five that need a person (`verdicts`, R4-R6). Which category a question is in is still
  Jera's word (`spec.md` C2); nothing here can check it.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

AGENT = "Jera"
VIA = "precedent"
ACTOR = f"agent:{AGENT}"

# `spec.md` R6, `intent.md ## Answers, câu 3 and câu 5`. A question in one of these is a
# person's, whatever precedent says.
NEEDS_PERSON = (
    "product-direction",
    "security-or-permissions",
    "significant-spend",
    "external-action",
    "business-tradeoff",
)
CATEGORIES = NEEDS_PERSON + ("other",)
ANSWER, PERSON = "answer", "needs-person"

# The last line of every block Jera writes; `cites_of` reads it back for the board.
CITES = "Tiền lệ:"

# `spec.md` R4. Said for a question the reply left out or said nothing usable about.
NOT_ANSWERED = "Jera did not answer this question."


def is_jera(name: Any) -> bool:
    """`spec.md` R11: the one name a person may not answer under."""
    return str(name or "").strip().lower() == AGENT.lower()


def entry_id(unit: str, artifact: str, n: Any) -> str:
    return f"{unit}/{artifact}#Câu {n}"


def entries(units: Iterable[dict[str, Any]], prefs_text: str, exclude_units: Iterable[str]) -> list[dict[str, str]]:
    """`spec.md` R8. `[{id, text}]`: each paragraph of the Settings text as `pref:<k>`, then
    each answer in force the board read carries (`answers`, `coscc/board.py`).

    Left out: an answer Jera gave (by its name or by `Via: precedent`), and every answer of a
    unit in `exclude_units` — the asked unit itself, so a question never cites its own
    answer. Leif's answers stay in (`spec.md` C3)."""
    out: list[dict[str, str]] = []
    paragraphs = [p.strip() for p in re.split(r"\n[ \t]*\n", prefs_text or "") if p.strip()]
    for k, p in enumerate(paragraphs, 1):
        out.append({"id": f"pref:{k}", "text": p})
    excluded = set(exclude_units)
    for u in units:
        name = str(u.get("name") or "")
        if name in excluded:
            continue
        for a in u.get("answers") or []:
            if is_jera(a.get("by")) or str(a.get("via") or "") == VIA:
                continue
            out.append({
                "id": entry_id(name, str(a.get("artifact") or ""), a.get("n")),
                "text": f"Question: {str(a.get('question') or '').strip()}\n"
                        f"Answer ({str(a.get('by') or '').strip()}): {str(a.get('text') or '').strip()}",
            })
    return out


def asked(unit: dict[str, Any]) -> list[dict[str, Any]]:
    """`spec.md` R2. The questions Jera is given: not answered, not in `review.md`, on a unit
    that is neither finished nor closed. `[{unit, artifact, n, text}]`."""
    action = str(unit.get("next") or "")
    if action == "finished" or action.startswith("closed"):
        return []
    return [
        {"unit": str(unit.get("name") or ""), "artifact": str(q["artifact"]), "n": int(q["n"]),
         "text": str(q.get("text") or "")}
        for q in unit.get("questions") or []
        if not q.get("answered") and q.get("artifact") != "review.md"
    ]


def build_prompt(questions: list[dict[str, Any]], store: list[dict[str, str]]) -> str:
    """`spec.md` Design 4. In English; the text Jera writes is Vietnamese because it goes into
    `.cos/`. No path and no workspace name: only questions and the store, by id."""
    lines = [
        f"You are {AGENT}. You answer the open questions of one unit of work from precedent: "
        "decisions already made in this project, listed under *Precedent* below. You have no "
        "tools and one turn; everything you may use is in this prompt.",
        "",
        "For every question under *Questions*, give exactly one verdict:",
        "- `answer`: the precedent settles it. `cites` lists the id of every precedent entry "
        "you relied on, copied exactly. An answer with no citation, or one citing an id not "
        "listed below, is not written.",
        "- `needs-person`: it needs the person who started this work. `text` is your proposed "
        "answer all the same, and `reason` says in one line why a person must decide.",
        "",
        "`category` is one of: " + ", ".join(f"`{c}`" for c in CATEGORIES) + ". Every question "
        "that is about " + ", ".join(f"`{c}`" for c in NEEDS_PERSON) + " is `needs-person`, "
        "whatever precedent says; use `other` for everything else.",
        "",
        "Best practice, common sense or what most teams do is not precedent. Only the entries "
        "below are, including the `pref:` ones the person wrote. When nothing below settles a "
        "question, it is `needs-person`.",
        "",
        "Write `text` and `reason` in Vietnamese: they are added to the unit's files. No line of "
        "`text` may start with `#`.",
        "",
        "Reply with one JSON block, and nothing in it but the list:",
        "```json",
        '[{"artifact":"spec.md","n":1,"verdict":"answer","category":"other","text":"...",'
        '"reason":"","cites":["pref:1"]}]',
        "```",
        "",
        "## Questions",
    ]
    for q in questions:
        lines += ["", f"### {q['artifact']}, question {q['n']}", str(q.get("text") or "").strip()]
    lines += ["", "## Precedent"]
    if not store:
        lines += ["", "(none)"]
    for e in store:
        lines += ["", f"### {e['id']}", e["text"]]
    return "\n".join(lines) + "\n"


_JSON_BLOCK = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def _first_list(reply: str) -> list[Any] | None:
    """The first fenced block that parses as a JSON list, else the whole reply if it does."""
    for raw in [m.group(1) for m in _JSON_BLOCK.finditer(reply or "")] + [reply or ""]:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("questions"), list):
            data = data["questions"]
        if isinstance(data, list):
            return data
    return None


def _number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def verdicts(reply: str, questions: list[dict[str, Any]], entry_ids: Iterable[str]) -> dict[str, Any]:
    """`spec.md` R4, R5, R6. `{failed, verdicts, ignored}`.

    `failed` is a reason when the reply holds no JSON list, and then nothing else is read.
    Otherwise `verdicts` has one element per question in `questions`, in their order, each
    `{artifact, n, question, verdict, category, text, reason, cites}`. An element naming a
    question not in `questions` — any `review.md`, any `F<n>` — is `ignored` and reaches
    nothing. Every downgrade to `needs-person` says why in `reason`.
    """
    data = _first_list(reply)
    if data is None:
        return {"failed": "the reply holds no JSON list", "verdicts": [], "ignored": []}
    known = set(entry_ids)
    wanted = {(q["artifact"], int(q["n"])): q for q in questions}
    said: dict[tuple[str, int], dict[str, Any]] = {}
    ignored: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            ignored.append({"item": str(item)[:200], "reason": "not an object"})
            continue
        at = (str(item.get("artifact") or ""), _number(item.get("n")))
        if at not in wanted:
            ignored.append({"artifact": at[0], "n": item.get("n"), "reason": "not a question Jera was asked"})
            continue
        if at in said:
            ignored.append({"artifact": at[0], "n": at[1], "reason": "a second verdict for one question"})
            continue
        said[at] = item

    out = []
    for at, q in wanted.items():
        item = said.get(at)
        base = {"artifact": at[0], "n": at[1], "question": q.get("text", "")}
        if item is None:
            out.append({**base, "verdict": PERSON, "category": "", "text": "", "reason": NOT_ANSWERED, "cites": []})
            continue
        verdict = item.get("verdict")
        category = item.get("category")
        text = item.get("text")
        reason = item.get("reason")
        cites = item.get("cites")
        text = text.strip() if isinstance(text, str) else ""
        reason = reason.strip() if isinstance(reason, str) else ""
        category = category.strip() if isinstance(category, str) else ""
        well_formed = (
            verdict in (ANSWER, PERSON)
            and isinstance(item.get("text"), str)
            and (cites is None or (isinstance(cites, list) and all(isinstance(c, str) for c in cites)))
        )
        cites = list(dict.fromkeys(c.strip() for c in cites if c.strip())) if isinstance(cites, list) and well_formed else []
        v = {**base, "verdict": verdict, "category": category, "text": text, "reason": reason, "cites": cites}
        if not well_formed:
            v.update(verdict=PERSON, reason=NOT_ANSWERED)
        elif verdict == ANSWER:
            unknown = [c for c in cites if c not in known]
            why = ""
            if not text:
                why = "The answer is empty."
            elif not cites:
                why = "It cites no precedent."
            elif unknown:
                why = f"It cites {', '.join(unknown)}, which is not in the precedent it was given."
            elif category in NEEDS_PERSON:
                why = f"Its category, {category}, needs a person."
            elif category not in CATEGORIES:
                why = f"Its category, {category or '(none)'}, is not one the app knows."
            elif any(line.lstrip().startswith("#") for line in text.splitlines()):
                why = "A line of it starts with #, which an answer may not."
            if why:
                v.update(verdict=PERSON, reason=why)
        if v["verdict"] == PERSON and not v["reason"]:
            v["reason"] = "Jera gave no reason."
        if v["verdict"] == PERSON and not v["text"]:
            v["reason"] = (v["reason"] + " " if v["reason"] else "") + "Its proposal is empty."
        out.append(v)
    return {"failed": None, "verdicts": out, "ignored": ignored}


def block_text(v: dict[str, Any]) -> str:
    """The body of the `### Câu N` block for one `answer` verdict: its words, then its cites."""
    return f"{v['text']}\n\n{CITES} {'; '.join(v['cites'])}"


def cites_of(text: str) -> list[str]:
    """The citations of a block `block_text` wrote, read back from its last `Tiền lệ:` line."""
    for line in reversed((text or "").splitlines()):
        if line.startswith(CITES):
            return [c.strip() for c in line[len(CITES):].split(";") if c.strip()]
    return []


async def ask(sessions: Any, cwd: str, prompt: str, grant: Any, model: str | None,
              effort: str | None) -> tuple[str, dict[str, Any], str]:
    """One tool-less session, as `Service.propose_estimates` runs its own. `(reply, end,
    failure)`: `end` holds `session_id`, `cost` and `terminal_reason`; `failure` is `""`
    unless the session broke or stopped at a ceiling, which counts as broken."""
    from coscc.runner import CEILING_MARKERS, Denials, permission_gate
    from coscc.sessions import StepHandle

    reply, end, failure = "", {}, ""
    try:
        async for kind, payload in sessions.stream(
            cwd, prompt, None, max_turns=grant.max_turns, tools=[],
            # `tools=[]` still lets MCP tools through (`sessions.py`); the gate refuses them.
            can_use_tool=permission_gate(grant, cwd, Denials()),
            max_budget_usd=grant.max_budget_usd, step=StepHandle(),
            **({"model": model} if model is not None else {}),
            **({"effort": effort} if effort is not None else {}),
        ):
            if kind == "chunk":
                reply += payload
            elif kind == "session":
                end["session_id"] = str(payload)
            elif kind == "done":
                end.update(session_id=payload.get("session_id", end.get("session_id", "")),
                           cost=payload.get("cost") or {},
                           terminal_reason=str(payload.get("terminal_reason") or ""))
    except Exception as e:  # noqa: BLE001 — recorded as the reason
        failure = f"the session failed: {e}"
    terminal = end.get("terminal_reason", "")
    if not failure and any(m in terminal for m in CEILING_MARKERS):
        failure = f"the session stopped at a ceiling ({terminal}); nothing was written"
    return reply, end, failure
