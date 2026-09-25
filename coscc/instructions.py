"""The project's own instructions, read by the app rather than by the CLI.

Since `0088` every session the app opens runs with no settings source at all
(`coscc/sessions.py` `_options`), so the CLI no longer loads `CLAUDE.md` or the rules under
`.claude/rules/` by itself. What a stage needs of them comes back through here, into the
system prompt, and nothing else does:

- `<cwd>/CLAUDE.md` and `<cwd>/.claude/CLAUDE.md`, verbatim.
- Every `<cwd>/.claude/rules/**/*.md` whose front-matter has no `paths:`, verbatim.
- Every rule that has one, as one line of a table of contents and never its contents.
  `0088` `spike.md ## U7` measured that with no sources the CLI does not load a scoped rule
  even after the session reads a file it matches, so the session is told to `Read` it.

No parent directory, no `CLAUDE.local.md` (the `local` source is cut by the intent's
answer 1), nothing under `~/.claude/`. An `@path` stays the text it is, for the reason
`verbatim_prompts` is set: nothing this app sends may pull in a file by naming it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

HEADING = "# Project instructions"
SCOPED_HEADING = "## Scoped rules"

# Instructions to the model, so English (`.claude/CLAUDE.md`, *Invariants*).
SCOPED_LINE = (
    "- {path} (paths: {patterns}): read this file with Read before reading, editing or "
    "reviewing a file matching one of these patterns."
)

UNREADABLE = " (unreadable)"


@dataclass(frozen=True)
class Instructions:
    """What `read` found. `text` is empty when `cwd` holds none of it."""

    text: str = ""
    # Relative to `cwd`. A file that could not be read is here with `UNREADABLE` after it.
    verbatim: tuple[str, ...] = ()
    scoped: tuple[str, ...] = ()

    def record(self) -> dict[str, list[str]]:
        """The `instructions` field of a board step's `start` row (`0088` R13)."""
        return {"verbatim": list(self.verbatim), "scoped": list(self.scoped)}


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def scoped_patterns(text: str) -> list[str] | None:
    """The patterns under `paths:` in the front-matter, or `None` when there are none.

    Only the one key is looked for, by hand: the app has no YAML parser and this unit adds
    no dependency. Both `paths: ["a", "b"]` and a block of `  - "a"` lines are read.
    Anything this cannot make sense of -- a front-matter never closed, a `[` with no `]`,
    a `paths:` with nothing under it -- is `None`, so the file goes in whole: a typo costs
    context, never a rule.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None
    front = lines[1:end]
    for i, line in enumerate(front):
        if not line.startswith("paths:"):
            continue
        value = line[len("paths:"):].strip()
        if value.startswith("["):
            if not value.endswith("]"):
                return None
            found = [_strip_quotes(p) for p in value[1:-1].split(",")]
        elif value:
            found = [_strip_quotes(value)]
        else:
            found = []
            for item in front[i + 1:]:
                stripped = item.strip()
                if not item[:1].isspace() or not stripped.startswith("-"):
                    break
                found.append(_strip_quotes(stripped[1:]))
        found = [p for p in found if p]
        return found or None
    return None


def read(cwd: str | Path) -> Instructions:
    """Everything a session in `cwd` is given of its project's instructions (`0088` R5, R7)."""
    root = Path(cwd)
    candidates = [root / "CLAUDE.md", root / ".claude" / "CLAUDE.md"]
    rules = root / ".claude" / "rules"
    if rules.is_dir():
        candidates += sorted(
            (p for p in rules.rglob("*.md") if p.is_file()),
            key=lambda p: p.relative_to(root).as_posix(),
        )

    sections: list[str] = []
    verbatim: list[str] = []
    scoped: list[tuple[str, list[str]]] = []
    for path in candidates:
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        try:
            body = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            verbatim.append(name + UNREADABLE)
            continue
        # Only a rule can be scoped; a `CLAUDE.md` is always read whole, as the CLI did.
        patterns = scoped_patterns(body) if rules in path.parents else None
        if patterns is None:
            sections.append(f"## {name}\n\n{body}")
            verbatim.append(name)
        else:
            scoped.append((name, patterns))

    if not sections and not scoped:
        return Instructions()
    parts = [HEADING, *sections]
    if scoped:
        parts.append(
            SCOPED_HEADING
            + "\n\n"
            + "\n".join(
                SCOPED_LINE.format(path=name, patterns=", ".join(patterns))
                for name, patterns in scoped
            )
        )
    return Instructions(
        text="\n\n".join(parts),
        verbatim=tuple(verbatim),
        scoped=tuple(name for name, _ in scoped),
    )
