"""Release: cutting a release from the Work page.

Not a stage; writes no artifact. A release is two presses: *Prepare* opens a pull request that
changes the four declared version files and nothing else, and *Merge and tag* merges it once its
required checks are green and pushes `vX.Y.Z` onto the merge commit (the tag builds the release).

Every press leaves one `release` record in the run log. State is read again from git and `gh`
whenever the panel is read, never from memory.

Grammar is the loop's (`check-tag`, `check-branch`, `check-version` of `python -m coscc.loop`). The
regular expressions in `rules` only sort versions and keep a string from being read as a flag.

The pure rules are in `rules`; the `gh`, loop and `uv` calls come first here, then the panel and the
two presses (`Release`), then the three routes.
"""

from __future__ import annotations

import asyncio
import json
import re
import tomllib
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from starlette.routing import BaseRoute

from coscc.features.release.rules import (
    BUTTON,
    TREE,
    Outcome,
    ReleaseError,
    ReleaseView,
    branch_name,
    candidates,
    classify,
    empty_block,
    extra_diff,
    match_commits,
    opened_head,
    own,
    pr_body,
    publish_problem,
    record,
    refusal,
    release_prs,
    set_version_text,
    version_problem,
)
from coscc.kernel import (
    BadRecord,
    Busy,
    Ctx,
    Feature,
    GitError,
    Invalid,
    Journal,
    body,
    child_env,
    commit_files,
    commits_between,
    create_branch,
    delete_merged_branch,
    detach_here,
    diff_u0,
    fetch_with_tags,
    gh,
    is_ancestor,
    ndjson,
    own_tree_remove,
    push_branch,
    push_tag,
    remote_has_tag,
    rev_parse,
    run,
    show_file,
    tags_merged,
    worktree_add,
    worktree_list,
)

# Seconds. `uv lock` resolves again and may reach the network. Chosen, not measured.
LOCK_TIMEOUT = 300.0
# Seconds. The loop child reads a few files and prints one line. Chosen, not measured.
COS_TIMEOUT = 30.0
# How long the app waits for GitHub to show a merge commit: as integrate's, chosen not measured.
POLL_TRIES = 5
POLL_DELAY = 2.0

_URL_NUMBER = re.compile(r"/pull/(\d+)\s*$")

PrsOnce = Callable[[], Awaitable["list[dict[str, Any]] | str"]]


async def _gh(argv: list[str], cwd: str) -> tuple[int, str, str]:
    """One `gh` call; one that could not be made is a `ReleaseError`."""
    got = await gh.call(gh.run, argv, cwd)
    if isinstance(got, str):
        raise ReleaseError(got)
    return got


async def merged_release_pr(root: str, branch: str) -> dict:
    """The merged pull request of one release branch, `{number, merge_sha, head}`."""
    code, out, err = await _gh(
        [
            "pr",
            "list",
            "--state",
            "merged",
            "--head",
            branch,
            "--json",
            "number,mergeCommit,headRefOid",
            "--limit",
            "5",
        ],
        root,
    )
    if code != 0:
        raise ReleaseError(gh.said(code, out, err))
    try:
        rows = json.loads(out or "[]")
    except ValueError as e:
        raise ReleaseError(f"gh pr list did not return JSON: {e}") from e
    for r in rows if isinstance(rows, list) else []:
        sha = str((r.get("mergeCommit") or {}).get("oid") or "")
        if sha:
            return {
                "number": r.get("number"),
                "merge_sha": sha,
                "head": str(r.get("headRefOid") or ""),
            }
    raise ReleaseError(f"no merged pull request was found for {branch}")


async def create_pr(tree: str, branch: str, title: str, body: str) -> int:
    """The pull request's number, read off the URL `gh` prints."""
    code, out, err = await _gh(
        ["pr", "create", "--base", "main", "--head", branch, "--title", title, "--body", body], tree
    )
    if code != 0:
        raise ReleaseError(gh.said(code, out, err))
    m = _URL_NUMBER.search(out.strip().splitlines()[-1] if out.strip() else "")
    if not m:
        raise ReleaseError(f"gh pr create printed no pull request URL: {out.strip()[:200]}")
    return int(m.group(1))


async def merge_pr(tree: str, n: int, head: str) -> None:
    """Squash, delete the branch, and only if the head is still the one the app pushed."""
    code, out, err = await _gh(
        ["pr", "merge", str(int(n)), "--squash", "--delete-branch", "--match-head-commit", head],
        tree,
    )
    if code != 0:
        raise ReleaseError(gh.said(code, out, err))


async def merge_commit(tree: str, n: int) -> str:
    """The merge commit, asked up to `POLL_TRIES` times."""
    said = ""
    for attempt in range(POLL_TRIES):
        code, out, err = await _gh(["pr", "view", str(int(n)), "--json", "state,mergeCommit"], tree)
        if code == 0:
            try:
                data = json.loads(out)
            except ValueError:
                data = {}
            sha = str((data.get("mergeCommit") or {}).get("oid") or "")
            if data.get("state") == "MERGED" and sha:
                return sha
            said = f"the pull request is {data.get('state') or 'unread'}"
        else:
            said = gh.said(code, out, err)
        if attempt + 1 < POLL_TRIES:
            await asyncio.sleep(POLL_DELAY)
    raise ReleaseError(f"no merge commit for #{n}: {said}")


async def release_status(root: str, tag: str) -> dict[str, str]:
    """`{release, release_url, workflow, workflow_url}`, each `""` when unread."""
    out_: dict[str, str] = {"release": "", "release_url": "", "workflow": "", "workflow_url": ""}
    try:
        code, out, _ = await _gh(["release", "view", tag, "--json", "url,isDraft"], root)
        if code == 0:
            data = json.loads(out or "{}")
            out_["release"] = "draft" if data.get("isDraft") else "published"
            out_["release_url"] = str(data.get("url") or "")
        code, out, _ = await _gh(
            [
                "run",
                "list",
                "--workflow",
                "release.yml",
                "--branch",
                tag,
                "--json",
                "status,conclusion,url",
                "--limit",
                "1",
            ],
            root,
        )
        rows = json.loads(out or "[]") if code == 0 else []
        if isinstance(rows, list) and rows:
            row = rows[0]
            out_["workflow"] = str(row.get("conclusion") or row.get("status") or "")
            out_["workflow_url"] = str(row.get("url") or "")
    except ReleaseError, ValueError, AttributeError:
        pass
    return out_


async def cos(where: Path, *args: str, timeout: float = COS_TIMEOUT) -> tuple[int, str]:
    """`python -m coscc.loop <args>` of this app with `where` as its cwd: the checkout's version
    files are read, its code is never run. `(exit code, stdout or stderr)`.
    """
    try:
        got = await run.ask(list(args), cwd=Path(where), timeout=timeout)
    except OSError as e:
        return 127, f"python -m coscc.loop could not be started: {e}"
    except TimeoutError:
        return 124, f"python -m coscc.loop did not finish within {timeout:.0f}s"
    text = got.out.strip() if got.code == 0 else (got.err.strip() or got.out.strip())
    return got.code, text


async def _run(argv: list[str], cwd: Path, timeout: float) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=child_env(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        return 127, f"{argv[0]} could not be started: {e}"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"{argv[0]} did not finish within {timeout:.0f}s"
    code = proc.returncode or 0
    text = (
        out.decode(errors="replace").strip()
        if code == 0
        else (err.decode(errors="replace").strip() or out.decode(errors="replace").strip())
    )
    return code, text


async def set_versions(tree: Path, old: str, new: str) -> None:
    """The three hand-edited files, then `uv lock`. No `uv sync`, `npm ci` or build."""
    for name in ("pyproject.toml", "package.json", "package-lock.json"):
        path = Path(tree) / name
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise ReleaseError(f"could not read {name}: {e}") from e
        path.write_text(set_version_text(name, text, old, new), encoding="utf-8")
    code, said = await _run(["uv", "lock"], Path(tree), LOCK_TIMEOUT)
    if code != 0:
        raise ReleaseError(f"uv lock failed: {said}")


# -- the Release block and its two presses ---------------------------------------


class Release:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        # Journal keys with a release press running now, checked and marked with no `await` between.
        self._releasing: set[str] = set()

    def _release_records(self, journal: Journal | None, key: str) -> list[dict[str, Any]]:
        if journal is None:
            return []
        try:
            return journal.records(key, kind="release")
        except Busy:
            return []

    def _prs_once(self, cwd: str, fresh: bool) -> PrsOnce:
        """The board's `gh pr list` answer for `cwd`, asked once however often it is awaited."""
        got: list[list[dict[str, Any]] | str] = []

        async def _prs() -> list[dict[str, Any]] | str:
            if not got:
                got.append(await self.ctx.units.open_prs(cwd, fresh))
            return got[0]

        return _prs

    async def _release_facts(
        self,
        root: Path,
        units_: list[dict[str, Any]],
        prs: PrsOnce,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """What git and `gh` say now, and the state `rules.classify` makes of it.

        Reads only, against the `origin/main` and tags the last fetch brought. A block whose `state`
        is `unknown` or `nothing` carries only its reason. `prs` is awaited only once a release tag
        is found, so a workspace never released asks `gh` nothing here.
        """
        try:
            origin = await rev_parse(root, "refs/remotes/origin/main")
            tags = await tags_merged(root, origin)
        except GitError as e:
            return {**empty_block("unknown", str(e))}
        last_tag = ""
        for tag in candidates(tags):
            code, said = await cos(root, "check-tag", tag)
            if code == 0 and said.strip() == "release":
                last_tag = tag
                break
        if not last_tag:
            return {**empty_block("nothing", "no release yet")}
        got = await prs()
        if isinstance(got, str):
            return {**empty_block("unknown", got), "last_tag": last_tag}
        open_prs = release_prs(got)
        try:
            tag_sha = await rev_parse(root, f"refs/tags/{last_tag}")
            commits = await commits_between(root, tag_sha, origin)
            main_version = str(
                tomllib.loads(await show_file(root, origin, "pyproject.toml", own(root)))
                .get("project", {})
                .get("version")
                or ""
            )
        except (GitError, tomllib.TOMLDecodeError) as e:
            return {**empty_block("unknown", str(e)), "last_tag": last_tag}
        matched = match_commits(commits, units_)
        verdict = classify(
            last_tag=last_tag,
            units=matched["units"],
            unmatched=matched["unmatched"],
            open_pr=open_prs[0] if open_prs else None,
            main_version=main_version,
            tags=tags,
            last_record=records[-1] if records else None,
        )
        block = {
            **empty_block(verdict["state"], verdict["reason"]),
            "last_tag": last_tag,
            "units": matched["units"],
            "unmatched": matched["unmatched"],
            "count": len(matched["units"]),
            "version": verdict["version"],
            "proposed": verdict["version"] if verdict["state"] == "ready" else "",
            "main_version": main_version,
            "origin_sha": origin,
            "tags": tags,
            "_prs": got,
        }
        return block

    async def _release_detail(
        self, root: Path, block: dict[str, Any], records: list[dict[str, Any]], fresh: bool
    ) -> None:
        """The button, whether it may be pressed, and the workflow. Only the state that needs one asks
        `gh`, through `ctx.asks`: waited on the first time a pull request's head or a tag is asked,
        or when `fresh`; else held and asked again in the background.
        """
        state = block["state"]
        block["button"] = BUTTON.get(state, "")
        block["enabled"] = bool(block["button"])
        if state == "pr-open":
            open_pr = release_prs(block.pop("_prs", []) or [])
            row = open_pr[0] if open_pr else {}
            number = row.get("number")
            block["pr"] = number
            block["head"] = str(row.get("headRefOid") or "")

            async def _ask_checks(number: Any) -> list[dict[str, Any]] | str:
                try:
                    return await self.ctx.required_checks(str(root), int(number))
                except (TypeError, ValueError) as e:
                    return str(e)

            checks: list[dict[str, Any]] | str = (
                "no open release pull request"
                if number is None
                else await self.ctx.asks.get(
                    (str(root), "checks", str(number), block["head"]),
                    lambda: _ask_checks(number),
                    fresh,
                )
            )
            block["checks"] = checks if isinstance(checks, list) else []
            opened, _ = opened_head(records, block["version"])
            why = publish_problem(
                checks,
                block["head"],
                opened,
                f"v{block['version']}" in block.get("tags", []),
                block["version"],
            )
            block["enabled"], block["disabled_reason"] = not why, why
        elif state == "tagged":
            tag = block["last_tag"]
            status = await self.ctx.asks.get(
                (str(root), "status", tag), lambda: release_status(str(root), tag), fresh
            )
            block.update(status)
            if status["release"] == "published":
                block["state"], block["reason"] = "published", f"{block['last_tag']} is published"

    async def view(self, cwd: str, fresh: bool = False) -> dict[str, Any] | None:
        """The `release` block, or None for a workspace that is not a git checkout.

        Costs, at most: `git` reads, one `coscc.loop check-tag` per candidate tag, and once a
        release tag is found the board's held `gh pr list`, then `gh pr checks` on an open release
        pull request or `gh release view` and `gh run list` after a tag this app pushed; each `gh`
        answer is held, and waited on only the first time or when `fresh`. No fetch.
        """
        root = Path(cwd).expanduser().resolve()
        if not (root / ".git").exists():
            return None
        key = self.ctx.units.key(cwd)
        records = self._release_records(self.ctx.runs.journal(), key)
        units = await self.ctx.units.units(cwd, False)
        block = await self._release_facts(root, units, self._prs_once(cwd, fresh), records)
        await self._release_detail(root, block, records, fresh)
        for k in ("_prs", "tags", "origin_sha"):
            block.pop(k, None)
        return block

    def _release_start(self, cwd: str) -> tuple[Journal, str, Path]:
        key = self.ctx.units.key(cwd)
        self.ctx.refuse_updating()
        journal = self.ctx.runs.journal()
        if journal is None:
            raise Invalid(
                "no working folder is set, so a release cannot be recorded — set COS_WORKING_DIR"
            )
        return journal, key, Path(cwd).expanduser().resolve()

    def release_tree_path(self, cwd: str) -> Path:
        """The release tree of `cwd`, worked out again on every call: it is the `own.path`
        each writing function checks the tree it was handed against.
        """
        return self.ctx.units.own_tree(cwd, TREE)

    async def _release_tree_fresh(self, cwd: str, root: Path, sha: str) -> Path:
        """The release worktree, detached at `sha`; a tree left over is removed first."""
        tree = self.release_tree_path(cwd)
        await self._release_tree_gone(cwd, root, tree)
        tree.parent.mkdir(parents=True, exist_ok=True)
        await worktree_add(root, tree, sha)
        return tree

    async def _release_tree_gone(self, cwd: str, root: Path, tree: Path) -> None:
        listed = {str(Path(t["path"]).resolve()) for t in await worktree_list(root)}
        if str(tree.resolve()) in listed:
            await own_tree_remove(root, tree, own(self.release_tree_path(cwd)))

    async def _release_press(  # noqa: PLR0915 - still to split
        self,
        cwd: str,
        phase: str,
        version: str,
    ) -> tuple[Journal, str, Path, dict[str, Any], Path, str, Callable[..., dict[str, Any]]]:
        """Everything asked before a press changes anything: the fetch, the facts, the release tree at
        `origin/main` and the loop there. Refuses with one `refused` record.
        """
        journal, key, root = self._release_start(cwd)
        version = str(version or "").strip()
        fields: dict[str, Any] = {}

        def write(outcome: Outcome, **more: Any) -> dict[str, Any]:
            rec = record(
                workspace=key, phase=phase, version=version, outcome=outcome, **{**fields, **more}
            )
            try:
                return journal.append(rec)
            except BadRecord, Busy:
                return rec

        # Check-and-mark with no `await` between, as `Holds.take` does.
        if key in self._releasing:
            reason = refusal(
                active=True,
                phase=phase,
                open_release_pr=None,
                check_version=(0, ""),
                version_problem_="",
                state="",
            )
            write("refused", detail=reason)
            raise Invalid(reason)
        self._releasing.add(key)
        try:
            try:
                await fetch_with_tags(root)
                units = await self.ctx.units.units(cwd, True)
            except (GitError, Invalid) as e:
                write("failed", detail=f"could not read the workspace: {e}")
                raise Invalid(f"could not read the workspace: {e}") from e
            # Asked before the facts, tag or no tag: an open release pull request is a refusal reason.
            # `_release_facts` gets the same answer, not a second call.
            prs_once = self._prs_once(cwd, True)
            prs = await prs_once()
            records = self._release_records(journal, key)
            facts = await self._release_facts(root, units, prs_once, records)
            fields.update(
                proposed=facts["proposed"],
                last_tag=facts["last_tag"],
                units=facts["units"],
                commits=facts["unmatched"],
            )
            open_rel = release_prs(prs) if isinstance(prs, list) else []
            tree: Path | None = None
            # Asked in the tree below; `unreadable` refuses first when there is none.
            check_version = (1, "")
            problem = ""
            if facts["state"] != "unknown":
                try:
                    tree = await self._release_tree_fresh(cwd, root, facts["origin_sha"])
                except GitError as e:
                    write("failed", detail=f"could not open the release worktree: {e}")
                    raise Invalid(f"could not open the release worktree: {e}") from e
                check_version = await cos(tree, "check-version")
                code, said = await cos(tree, "check-tag", f"v{version}")
                on_remote = False
                if code == 0 and said.strip() == "release":
                    try:
                        on_remote = await remote_has_tag(tree, f"v{version}")
                    except GitError as e:
                        code, said = 1, f"the remote's tags could not be read: {e}"
                problem = version_problem(version, code, said, facts["last_tag"], on_remote)
                if not problem and phase == "publish" and version != facts["version"]:
                    problem = f"the release waiting to be tagged is {facts['version'] or 'none'}, not {version}"
            reason = refusal(
                active=False,
                phase=phase,
                open_release_pr=open_rel[0] if open_rel else None,
                check_version=check_version,
                version_problem_=problem,
                state=facts["state"],
                unreadable=facts["reason"] if facts["state"] == "unknown" else "",
            )
            checked: dict[str, Any] = {}
            if not reason and facts["state"] == "pr-open":
                row = open_rel[0]
                checks = await self.ctx.required_checks(str(root), int(row["number"]))
                opened, _ = opened_head(records, version)
                reason = publish_problem(
                    checks,
                    str(row.get("headRefOid") or ""),
                    opened,
                    f"v{version}" in facts.get("tags", []),
                    version,
                )
                fields.update(pr=row.get("number"), head=str(row.get("headRefOid") or ""))
                # The merge is pinned to the head just checked, which is the one the app pushed; the pull
                # request is not read a second time.
                checked = {"checked_pr": int(row["number"]), "checked_head": opened}
            if reason:
                if tree is not None:
                    try:
                        await self._release_tree_gone(cwd, root, tree)
                    except GitError:
                        pass
                write("refused", detail=reason)
                raise Invalid(reason)
            assert tree is not None
            old = check_version[1].split()[0] if check_version[1].split() else ""
            return journal, key, root, {**facts, **checked, "old": old}, tree, version, write
        except BaseException:
            self._releasing.discard(key)
            raise

    async def release_prepare(self, cwd: str, version: str) -> AsyncIterator[tuple[str, Any]]:
        """The first press: a `chore/release-X-Y-Z` pull request that changes the four version files
        and nothing else. One `release` record whatever happens.
        """
        journal, key, root, facts, tree, version, write = await self._release_press(
            cwd, "prepare", version
        )
        branch = branch_name(version)
        cut = False
        head = ""
        try:
            code, said = await cos(tree, "check-branch", branch)
            if code != 0:
                # Still before anything changed, so a 400 like every other refusal.
                write("refused", detail=f"check-branch refused {branch}: {said}")
                try:
                    await self._release_tree_gone(cwd, root, tree)
                except GitError:
                    pass
                raise Invalid(f"check-branch refused {branch}: {said}")
            await create_branch(tree, branch, facts["origin_sha"])
            cut = True
            await set_versions(tree, facts["old"], version)
            code, said = await cos(tree, "check-version")
            if code != 0 or (said.split() or [""])[0] != version:
                raise ReleaseError(f"check-version printed {said!r}, not {version}")
            extra = extra_diff(
                await diff_u0(tree, own(self.release_tree_path(cwd))), facts["old"], version
            )
            if extra:
                raise ReleaseError("the change is more than the version lines: " + "; ".join(extra))
            head = await commit_files(
                tree, own(self.release_tree_path(cwd)), f"chore(release): {version}"
            )
            await push_branch(tree, own(self.release_tree_path(cwd)), branch)
            number = await create_pr(
                str(tree),
                branch,
                f"chore(release): {version}",
                pr_body(version, facts["units"], facts["unmatched"]),
            )
            await detach_here(tree, own(self.release_tree_path(cwd)))
            rec = write("opened", pr=number, head=head)
            yield ("done", {"release": rec})
        except (GitError, ReleaseError) as e:
            rec = write("failed", detail=str(e))
            try:
                await self._release_tree_gone(cwd, root, tree)
            except GitError:
                pass
            if cut:
                # Only where the app left it: at `origin/main`, or at its own commit that changes the version
                # and nothing else. Left behind, the next Prepare of this version stops at `create_branch`.
                try:
                    await delete_merged_branch(root, branch, head or facts["origin_sha"])
                except GitError:
                    pass
            yield ("done", {"release": rec})
        finally:
            self._releasing.discard(key)

    async def release_publish(self, cwd: str, version: str) -> AsyncIterator[tuple[str, Any]]:
        """The second press: merge the release pull request, then tag its merge commit. From
        `merged-untagged` it starts at the tag. Never started by anything but a request.
        """
        journal, key, root, facts, tree, version, write = await self._release_press(
            cwd, "publish", version
        )
        tag = f"v{version}"
        merged: dict[str, Any] = {}
        try:
            if facts["state"] == "pr-open":
                number, head = facts["checked_pr"], facts["checked_head"]
                await merge_pr(str(tree), number, head)
                merged = {
                    "pr": number,
                    "head": head,
                    "merge_sha": await merge_commit(str(tree), number),
                }
            else:
                found = await merged_release_pr(str(root), branch_name(version))
                merged = {
                    "pr": found["number"],
                    "head": found["head"],
                    "merge_sha": found["merge_sha"],
                }
            sha = merged["merge_sha"]
            await fetch_with_tags(root)
            origin = await rev_parse(root, "refs/remotes/origin/main")
            if not await is_ancestor(root, sha, origin):
                raise ReleaseError(f"the merge commit {sha[:7]} is not on origin/main")
            tree = await self._release_tree_fresh(cwd, root, sha)
            code, said = await cos(tree, "check-version")
            if code != 0 or (said.split() or [""])[0] != version:
                raise ReleaseError(f"check-version at {sha[:7]} printed {said!r}, not {version}")
            await push_tag(tree, own(self.release_tree_path(cwd)), tag, sha)
            rec = write("tagged", **merged)
            try:
                await self._release_tree_gone(cwd, root, tree)
                # The tag was made on the remote only; the board reads local tags.
                await fetch_with_tags(root)
            except GitError:
                pass
            yield ("done", {"release": rec})
        except (GitError, ReleaseError, IndexError, KeyError) as e:
            rec = write("merged" if merged else "failed", detail=str(e), **merged)
            try:
                await self._release_tree_gone(cwd, root, tree)
            except GitError:
                pass
            yield ("done", {"release": rec})
        finally:
            self._releasing.discard(key)


def routes(ctx: Ctx) -> Sequence[BaseRoute]:
    router = APIRouter()
    rel = Release(ctx)

    async def _asked(request: Request) -> tuple[str, str]:
        """`(cwd, version)` of a press, refused before anything is read or written while the
        feature is off for the workspace."""
        got = await body(request)
        cwd = str(got.get("cwd", ""))
        ctx.units.key(cwd)
        if not ctx.settings.enabled(cwd):
            raise Invalid("release is off for this workspace")
        return cwd, str(got.get("version", ""))

    @router.get("/api/release", response_model=ReleaseView | None)
    async def get_release(request: Request) -> Any:
        """What a release of one workspace would gather and the one button it offers now; `null`
        for a workspace that is not a git checkout. Read from the board held."""
        cwd = request.query_params.get("cwd", "")
        ctx.units.key(cwd)
        if not ctx.settings.enabled(cwd):
            return None
        return await rel.view(cwd)

    @router.post("/api/release/prepare")
    async def release_prepare(request: Request) -> Any:
        """`{cwd, version}`: a `chore/release-X-Y-Z` pull request, streamed like `/api/units/integrate`.

        Whoever holds the password or a session can make this machine's `gh` login commit,
        push a branch and open a pull request. A refusal is a 400 before anything changes;
        every press leaves one `release` record."""
        cwd, version = await _asked(request)
        return await ndjson(rel.release_prepare(cwd, version), "the release")

    @router.post("/api/release/publish")
    async def release_publish(request: Request) -> Any:
        """`{cwd, version}`: merge the release pull request and push `vX.Y.Z` onto its merge
        commit, which publishes the release.

        Whoever holds the password or a session can make this machine's `gh` login merge into
        `main` and push a tag no ruleset protects. A refusal is a 400 before anything changes;
        every press leaves one `release` record."""
        cwd, version = await _asked(request)
        return await ndjson(rel.release_publish(cwd, version), "the release")

    return router.routes


FEATURE = Feature(
    "release",
    routes,
    default="on",
    summary="Prepares and publishes a release of the project from the Work page.",
)
