"""Finding secret values in bytes: the five forms a value may take, masking and counting.

A value is looked for whole, never in part: a piece of a value is not a value. `mask` and `scan`
read the same patterns, so what one hides the other reports.
"""

from __future__ import annotations

import base64
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import quote, quote_plus

# A derived form shorter than this is left out: it would match ordinary text. The raw value is
# always looked for.
MIN_DERIVED = 4


@dataclass(frozen=True)
class Hit:
    name: str
    where: str
    form: str


def _base64(value: bytes) -> list[bytes]:
    """Standard and URL-safe, padded and not, at each of the three byte offsets a value can sit at in
    a longer base64 text. The characters a neighbouring byte shapes are left off, so the pattern
    holds wherever the value is."""
    out: list[bytes] = []
    for encode in (base64.b64encode, base64.urlsafe_b64encode):
        for lead in (0, 1, 2):
            text = encode(b"\0" * lead + value)[(8 * lead + 5) // 6 :]
            bare = text.rstrip(b"=")
            inner = bare if (lead + len(value)) % 3 == 0 else bare[:-1]
            out += [text, bare, inner]
    return out


def _url(value: bytes) -> list[bytes]:
    return [f(value, safe=safe).encode() for f in (quote, quote_plus) for safe in ("", "/")]


def _json(value: bytes) -> list[bytes]:
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        return []
    return [json.dumps(text, ensure_ascii=a)[1:-1].encode() for a in (True, False)]


def forms(value: bytes) -> dict[str, list[bytes]]:
    """The patterns one value is found by, keyed `raw`, `base64`, `hex`, `url` and `json`."""
    if not value:
        return {k: [] for k in ("raw", "base64", "hex", "url", "json")}
    derived = {
        "base64": _base64(value),
        "hex": [value.hex().encode(), value.hex().upper().encode()],
        "url": _url(value),
        "json": _json(value),
    }
    return {
        "raw": [value],
        **{k: [p for p in dict.fromkeys(v) if len(p) >= MIN_DERIVED] for k, v in derived.items()},
    }


def _patterns(values: dict[str, bytes]) -> list[tuple[bytes, str, str]]:
    """`(pattern, name, form)`, one row a pattern (the first secret and form that has it), longest first."""
    seen: dict[bytes, tuple[str, str]] = {}
    for name, value in values.items():
        for form, found in forms(value).items():
            for pattern in found:
                seen.setdefault(pattern, (name, form))
    ordered = sorted(seen, key=len, reverse=True)
    return [(p, *seen[p]) for p in ordered]


def mask(values: dict[str, bytes], data: bytes) -> tuple[bytes, dict[str, int]]:
    """`data` with every pattern of every value replaced by `[secret:<name>]`, and how many times
    each name was replaced. One pass, longest pattern first, so what was written is not read again."""
    rows = _patterns(values)
    if not rows:
        return data, {}
    owner = {p: name for p, name, _ in rows}
    counts: Counter[str] = Counter()

    def swap(found: re.Match[bytes]) -> bytes:
        name = owner[found.group(0)]
        counts[name] += 1
        return b"[secret:" + name.encode() + b"]"

    return re.compile(b"|".join(re.escape(p) for p, _, _ in rows)).sub(swap, data), dict(counts)


def scan(values: dict[str, bytes], sources: Iterable[tuple[str, bytes]]) -> list[Hit]:
    """Where a value shows: a `Hit` for each secret, source and form that has one."""
    rows = _patterns(values)
    hits: dict[Hit, None] = {}
    for where, data in sources:
        for pattern, name, form in rows:
            if pattern in data:
                hits[Hit(name, where, form)] = None
    return list(hits)
