#!/usr/bin/env python3
"""`0046`: a release cut from the board, both presses, against a bare remote and a fake `gh`.

No session, no quota, no network. Every workspace is a `Fixture` of
`coscc/service/release_service_test.py`: a clone of a bare-directory remote holding
`cos.mjs`, the four version files at `0.1.0`, the tag `v0.1.0`, then a `feat` of unit A
(#11), a `fix` of unit B (#12) and a `build(deps)` of no unit (#13). The fake `gh` squashes
for real and pushes to the remote's `main`; it does nothing to local branches (plan Risk 3).

Exit 0 when every claim prints PASS, 1 when one prints FAIL, 2 when `node`, `uv` or `git`
is missing.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, say  # noqa: E402

from coscc.git import fetches  # noqa: E402
from coscc.service import Invalid  # noqa: E402
from coscc.service.release_service_test import Fixture, git, press  # noqa: E402

FILES = ["package-lock.json", "package.json", "pyproject.toml", "uv.lock"]


class Workspace:
    """One fixture with its environment, its `Service`, and its presses counted."""

    def __init__(self, base: Path, **kw):
        base.mkdir(parents=True)
        self.fx = Fixture(base, **kw)
        self.presses = 0
        self.service = None
        self.fresh()
        self.fx.units(self.service)

    def env(self):
        return mock.patch.dict(os.environ, self.fx.env)

    def fresh(self):
        """A new `Service` on the same data root: the app, restarted."""
        with self.env():
            self.service = self.fx.service()
        self.key = self.service._journal_key(self.fx.cwd)

    def block(self) -> dict:
        with self.env(), mock.patch.object(fetches, "shared", fetches.Fetches()):
            return asyncio.run(self.service.board(self.fx.cwd))["release"]

    def press(self, phase: str, version: str) -> tuple[dict, str]:
        """`(record, refusal)`: the record the press wrote, and the 400's words if refused."""
        self.presses += 1
        with self.env(), mock.patch.object(fetches, "shared", fetches.Fetches()):
            try:
                return press(self.service, phase, self.fx.cwd, version), ""
            except Invalid as e:
                return self.records()[-1], str(e)

    def records(self) -> list[dict]:
        return self.service._journal().records(self.key, kind="release")

    def tree_gone(self) -> bool:
        return not any((Path(self.fx.env["COS_DATA_DIR"]) / "worktrees").glob("*/release"))


def check_version_at(fx: Fixture, ref: str, where: Path) -> str:
    git(fx.workspace, "fetch", "-q", "origin", f"+{ref}:refs/verify/{where.name}")
    git(fx.workspace, "worktree", "add", "-q", "--detach", str(where), f"refs/verify/{where.name}")
    out = subprocess.run(["node", str(where / ".claude/scripts/cos.mjs"), "check-version"],
                         capture_output=True, text=True).stdout.strip()
    git(fx.workspace, "worktree", "remove", "--force", str(where))
    return out


def main() -> int:
    missing = [t for t in ("node", "uv", "git") if not shutil.which(t)]
    if missing:
        print(f"cannot run: {', '.join(missing)} not on PATH")
        return EXIT_ENV
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        ws = Workspace(base / "a")
        fx = ws.fx

        # R1/R4.
        got = ws.block()
        ok &= say(
            got["state"] == "ready" and got["last_tag"] == "v0.1.0" and got["count"] == 2
            and sorted((u["type"], u["pr"]) for u in got["units"]) == [("feat", 11), ("fix", 12)]
            and [c["subject"] for c in got["unmatched"]] == ["build(deps): bump (#13)"]
            and got["proposed"] == "0.2.0",
            "R1/R4: v0.1.0, two units with their Type and PR, count 2, one commit with no unit, 0.2.0 proposed",
            f"{got['state']} {got['last_tag']} {got['count']} {got['proposed']}",
        )
        patch_only = Workspace(base / "b", feats=False).block()
        nothing = Workspace(base / "c", commits=False).block()
        ok &= say(
            patch_only["proposed"] == "0.1.1" and nothing["proposed"] == "" and nothing["button"] == ""
            and nothing["reason"] == "nothing new since v0.1.0",
            "R4: fix and build only propose 0.1.1; nothing new proposes nothing and has no button",
            f"{patch_only['proposed']!r} / {nothing['state']} {nothing['reason']!r}",
        )

        # R13, before anything changed.
        git(fx.seed, "switch", "-q", "-c", "side")
        (fx.seed / "side.txt").write_text("side\n", encoding="utf-8")
        git(fx.seed, "add", "-A")
        git(fx.seed, "commit", "-q", "-m", "side")
        git(fx.seed, "tag", "v0.3.0")
        git(fx.seed, "push", "-q", "origin", "v0.3.0")
        git(fx.seed, "switch", "-q", "main")
        refusals = [ws.press("prepare", v) for v in ("0.1.0-rc.1", "0.1.0", "0.3.0")]
        ok &= say(
            [said for _, said in refusals] != ["", "", ""]
            and "prerelease" in refusals[0][1] and "not greater" in refusals[1][1]
            and "already on the remote" in refusals[2][1]
            and [r["outcome"] for r in ws.records()] == ["refused"] * 3
            and not fx.remote_ref("refs/heads/chore/release-0-3-0") and ws.tree_gone(),
            "R13: prerelease, not above the tag, and a tag already on the remote are refused, one record each, nothing left",
            " | ".join(said for _, said in refusals),
        )

        # R6/R7.
        rec, said = ws.press("prepare", "0.2.0")
        branch = fx.remote_ref("refs/heads/chore/release-0-2-0")
        main_sha = fx.remote_ref("refs/heads/main")
        count = subprocess.run(["git", "--git-dir", str(fx.remote), "rev-list", "--count", f"{main_sha}..{branch}"],
                               capture_output=True, text=True).stdout.strip() if branch else ""
        names = sorted(subprocess.run(["git", "--git-dir", str(fx.remote), "diff", "--name-only", main_sha, branch],
                                      capture_output=True, text=True).stdout.split()) if branch else []
        printed = check_version_at(fx, "refs/heads/chore/release-0-2-0", base / "check") if branch else ""
        ok &= say(
            rec.get("outcome") == "opened" and branch == rec.get("head") and count == "1" and names == FILES
            and printed == "0.2.0",
            "R6/R7: one commit on origin/main, exactly the four version files, check-version prints 0.2.0, an opened record",
            f"{rec.get('outcome')} {said or rec.get('detail', '')} count={count} files={names} check-version={printed!r}",
        )

        # R13: an open release pull request comes before the version, whatever the version.
        _, said = ws.press("prepare", "0.1.0")
        ok &= say("already open" in said, "R13: a second Prepare while one is open is refused, before the version is judged", said)

        # R9.
        fx.set_checks("pending")
        waiting = ws.block()
        _, said = ws.press("publish", "0.2.0")
        ok &= say(
            not waiting["enabled"] and "still running" in waiting["disabled_reason"] and "still running" in said
            and "pr merge" not in " ".join(fx.calls()),
            "R9: checks still running close Merge and tag, on the board and on a press",
            f"{waiting['disabled_reason']!r} / {said!r}",
        )
        fx.set_checks("pass")
        ready = ws.block()
        rec, said = ws.press("publish", "0.2.0")

        # R10.
        tag = fx.remote_ref("refs/tags/v0.2.0")
        main_now = fx.remote_ref("refs/heads/main")
        on_main = subprocess.run(["git", "--git-dir", str(fx.remote), "merge-base", "--is-ancestor", tag or "0" * 40,
                                  main_now], capture_output=True).returncode == 0
        ok &= say(
            ready["enabled"] and rec.get("outcome") == "tagged" and tag == rec.get("merge_sha") and on_main
            and ws.tree_gone(),
            "R10: with checks green, v0.2.0 is pushed onto the merge commit, which is on main",
            f"{rec.get('outcome')} {said or rec.get('detail', '')} tag={tag[:7]} merge={str(rec.get('merge_sha'))[:7]}",
        )

        # R11.
        after = ws.block()
        ok &= say(after["state"] == "published" and after["workflow"] == "success",
                  "R11: after the tag the panel reads published, with the release workflow",
                  f"{after['state']} {after['workflow']}")

        # R15.
        ok &= say(len(ws.records()) == ws.presses,
                  "R15: exactly one release record per press", f"{len(ws.records())} records, {ws.presses} presses")

        # R12: the press merges, the remote refuses the tag, the app restarts.
        ws2 = Workspace(base / "d")
        ws2.press("prepare", "0.2.0")
        ws2.fx.set_checks("pass")
        hook = ws2.fx.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nif grep -q refs/tags/; then echo 'tags refused here' >&2; exit 1; fi\n",
                        encoding="utf-8")
        hook.chmod(0o755)
        half, _ = ws2.press("publish", "0.2.0")
        hook.unlink()
        ws2.fresh()
        state = ws2.block()
        rec2, said = ws2.press("publish", "0.2.0")
        merges = [c for c in ws2.fx.calls() if c.startswith("pr merge")]
        ok &= say(
            half.get("outcome") == "merged" and state["state"] == "merged-untagged" and state["button"] == "publish"
            and rec2.get("outcome") == "tagged" and ws2.fx.remote_ref("refs/tags/v0.2.0") == rec2.get("merge_sha")
            and len(merges) == 1,
            "R12: merged but not tagged, a restarted app reads merged-untagged and tags without merging again",
            f"{half.get('outcome')} / {state['state']} / {rec2.get('outcome')} {said or rec2.get('detail', '')} merges={len(merges)}",
        )

        # R7, refused: `uv lock` changes a line that is not the version.
        ws3 = Workspace(base / "e")
        lock = ws3.fx.seed / "uv.lock"
        lock.write_text(lock.read_text(encoding="utf-8").replace(">=3.11", ">=3.10"), encoding="utf-8")
        git(ws3.fx.seed, "commit", "-q", "-am", "chore: skew the lock")
        git(ws3.fx.seed, "push", "-q", "origin", "main")
        rec, said = ws3.press("prepare", "0.2.0")
        ok &= say(
            rec.get("outcome") == "failed" and "uv.lock" in str(rec.get("detail"))
            and not ws3.fx.remote_ref("refs/heads/chore/release-0-2-0") and ws3.tree_gone()
            and not any(c.startswith("pr create") for c in ws3.fx.calls()),
            "R7: a lock uv rewrites beyond the version is refused: no commit pushed, no pull request, no tree, the extra diff recorded",
            f"{rec.get('outcome')} {str(rec.get('detail'))[:160]}",
        )
    return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
