"""`pr.md`'s title and body, put onto its pull request after a `pr` step (`0055`).

**What this module may do on GitHub, and nothing more.** It reads a pull request's title and
body (`gh pr view <url> --json title,body`) and replaces them (`gh pr edit <url>
[--title=<title>] --body-file -`). No other flag: no `--base`, no `--add-*`, no
`--remove-*`, nothing that merges, closes or comments. The title and body are what
`cos.mjs pr-text` cut from the unit's own `pr.md`; no caller can hand it other words, and
the service never passes anything else.

**It overwrites.** A description a person edited on GitHub after the step is replaced, and
the old text is kept nowhere (`intent.md ## Answers, câu 3`: `pr.md` is the source). When
both already match, nothing is written.

**Why `--title=<title>` is one argv.** A title that begins with `-` then cannot be read as a
flag. The URL cannot either: it is checked against `prcomment.PR_URL_RE` first.

`sync` never raises. A failure comes back as `Result("failed", reason=...)` with gh's own
words; `pr.md` is already written and nothing here touches it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from coscc import prcomment


@dataclass(frozen=True)
class Result:
    state: str  # updated | already | failed
    reason: str = ""

    def as_dict(self) -> dict:
        return {"state": self.state, "reason": self.reason}


def same(a: str, b: str) -> bool:
    """Equal once `\\r\\n` is `\\n` and trailing whitespace is gone. Nothing else is compared."""
    return (a or "").replace("\r\n", "\n").rstrip() == (b or "").replace("\r\n", "\n").rstrip()


def edit_argv(url: str, title: str | None) -> list[str]:
    """The one `gh pr edit` this module runs. The terminal line in `write-pr` builds the same."""
    return ["pr", "edit", url, *([f"--title={title}"] if title else []), "--body-file", "-"]


async def sync(
    url: str,
    title: str | None,
    body: str,
    cwd: str,
    run: prcomment.Run | None = None,
) -> Result:
    """Put `title` and `body` on the pull request at `url` unless they are there. Never raises.

    `title` None leaves the title on GitHub as it is. `run` defaults to `prcomment._gh`,
    looked up at call time so a test can replace it, and so the timeout is
    `prcomment.TIMEOUT` per call.
    """
    run = run or prcomment._gh
    if not url or not prcomment.PR_URL_RE.match(url):
        return Result("failed", reason=f"not a pull request URL: {url!r}")

    got = await prcomment._call(run, ["pr", "view", url, "--json", "title,body"], cwd, None)
    if isinstance(got, str):
        return Result("failed", reason=got)
    code, out, err = got
    if code != 0:
        return Result("failed", reason=prcomment._said(code, out, err))
    try:
        now = json.loads(out)
        now_title, now_body = str(now.get("title") or ""), str(now.get("body") or "")
    except (json.JSONDecodeError, ValueError, AttributeError) as e:
        return Result("failed", reason=f"gh pr view did not return JSON: {e}")
    if same(now_body, body) and (title is None or same(now_title, title)):
        return Result("already")

    got = await prcomment._call(run, edit_argv(url, title), cwd, body)
    if isinstance(got, str):
        return Result("failed", reason=got)
    code, out, err = got
    if code != 0:
        return Result("failed", reason=prcomment._said(code, out, err))
    return Result("updated")
