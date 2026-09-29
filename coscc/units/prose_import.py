"""Review rounds a `review.md` holds only in prose, read into `review_rounds` once per store.

A round is imported only when what `reviewFrom` rebuilds from the row reads exactly as the
file did; anything else stays in the file. It is written through `UnitMeta.record_round`.
Nothing here runs again once the store's key is in `migrations`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from coscc.data import Data
from coscc.units.meta import UnitMeta

SOURCE = "prose-import"
VERDICTS = ("pass", "changes-requested", "needs-person")
LABELS = ("open", "fixed", "needs-person", "claim-rejected", "answered")

# `cos.mjs` `SEVERITY`, whole: the location is one token.
_FINDING_TEXT = re.compile(r"(\S+)\s+—\s+(high|medium|low)\s+—\s+(.*)", re.IGNORECASE | re.DOTALL)
_LOCATION = re.compile(r"(.+?):(\d[\d,\-–]*)")
_RULE = re.compile(r"(S\d+)\s+(.*)", re.DOTALL)


def key(root: str, workspace: str) -> str:
    return f"{SOURCE}:0136-rounds:{root}/{workspace}"


def finding_of(f: Mapping[str, Any]) -> dict[str, Any] | None:
    """One finding as the `review-round` object carries it, or `None` when it would not read back the same."""
    m = _FINDING_TEXT.fullmatch(str(f.get("text") or "").strip())
    label = f.get("label")
    if m is None or label not in LABELS or not f.get("id"):
        return None
    location, severity, rest = m.groups()
    path, lines = "", ""
    if location != "(none)":
        at = _LOCATION.fullmatch(location)
        path, lines = (at.group(1), at.group(2)) if at else (location, "")
    rule = _RULE.fullmatch(rest)
    return {
        "id": str(f["id"]), "state": label, "fixed_in": str(f.get("fixed_by") or "") if label == "fixed" else "",
        "severity": severity.lower(), "rule": rule.group(1) if rule else "", "path": path, "lines": lines,
        "text": rule.group(2) if rule else rest,
    }


def round_of(r: Mapping[str, Any], heads: Mapping[str, str]) -> dict[str, Any] | None:
    """What `UnitMeta.record_round` takes, from one round `_rounds_of` carried; `heads` maps a prose SHA to the full one."""
    if r.get("verdict") not in VERDICTS or not isinstance(r.get("n"), int):
        return None
    findings = [finding_of(f) for f in r.get("found") or []]
    if any(f is None for f in findings):
        return None
    screens = r.get("screens")
    shots: list[dict[str, Any]] = []
    if screens:
        shots = [{k: s.get(k) for k in ("path", "size", "address", "result")} for s in screens.get("shots") or []]
        if not shots or not screens.get("taken"):
            return None
    reviewed = str(r.get("reviewed") or "")
    return {
        "n": r["n"], "run": SOURCE, "head": heads.get(reviewed, reviewed),
        "screens": {k: screens.get(k) for k in ("taken", "standard", "by")} if screens else {},
        "object": {"verdict": r["verdict"], "findings": findings, "screens": shots},
    }


def import_rounds(
    meta: UnitMeta, workspace: str, units_: Iterable[Mapping[str, Any]], heads: Mapping[str, str],
) -> list[tuple[str, int]] | None:
    """Once per store, in one transaction with its `migrations` mark. Returns `(unit, n)` per round imported, or `None` if already done."""
    k = key(meta.root, workspace)
    if meta.data.has_run(k):
        return None
    done: list[tuple[str, int]] = []
    with meta.data.write() as conn:
        if meta.data.has_run(k, conn):
            return None
        have = {
            (r["unit"], r["n"]) for r in conn.execute(
                "SELECT unit, n FROM review_rounds WHERE root = ? AND workspace = ?", (meta.root, workspace))
        }
        for u in units_:
            for r in u.get("rounds") or []:
                if (u["name"], r.get("n")) in have:
                    continue
                got = round_of(r, heads)
                if got is None:
                    continue
                meta.record_round(conn, workspace, u["name"], got)
                done.append((u["name"], got["n"]))
        Data.mark_run(conn, k)
    return done
