"""Cutting a release: the board's *Release* block and its two presses.

`coscc/github/release.py` holds the pure decisions and the `gh`, loop and `uv` calls, `coscc/git/gitops.py` the git ones.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, NotRequired, TypedDict

from coscc.github import integrate, release
from coscc.git import gitops
from coscc.units import worktrees
from coscc.git.gitops import GitError
from coscc.store.journal import BadRecord, Journal
from coscc.store.db import Busy
from coscc.units import BadUnit
from coscc.units import board as board_reader
from coscc.units.board import Unavailable
from coscc.service.update import refuse_while_updating
from coscc.service.common import CONSEQUENCE, Asked, open_prs_once
from coscc.kernel import Invalid

PrsOnce = Callable[[], Awaitable["list[dict[str, Any]] | str"]]
from coscc.config import Config
from coscc.service.workspaces import Workspaces
from coscc.update.updater import Updater


class ReleaseUnit(TypedDict):
    name: str
    type: str
    pr: int | None
    sha: str
    subject: str


class Commit(TypedDict):
    sha: str
    subject: str


class Check(TypedDict):
    name: str
    bucket: str


class ReleaseView(TypedDict):
    """The board's `release` block: what a release would gather since `last_tag`, the one
    button the state offers (`prepare` or `publish`) and why it may not be pressed."""

    state: str
    reason: str
    last_tag: str
    units: list[ReleaseUnit]
    unmatched: list[Commit]
    count: int
    proposed: str
    version: str
    pr: int | None
    checks: list[Check]
    head: str
    button: str
    enabled: bool
    disabled_reason: str
    warning: str
    consequence: str
    release_url: str
    workflow: str
    workflow_url: str
    main_version: NotRequired[str]


def _empty_block(state: str, reason: str) -> dict[str, Any]:
    return {
        "state": state,
        "reason": reason,
        "last_tag": "",
        "units": [],
        "unmatched": [],
        "count": 0,
        "proposed": "",
        "version": "",
        "pr": None,
        "checks": [],
        "head": "",
        "button": "",
        "enabled": False,
        "disabled_reason": "",
        "warning": release.WARNING,
        "consequence": CONSEQUENCE["release"],
        "release_url": "",
        "workflow": "",
        "workflow_url": "",
    }


class Release:
    def __init__(self, config: Config, ws: Workspaces, updater: Updater) -> None:
        self.config = config
        self.ws = ws
        self.updater = updater
        # Journal keys with a release press running now, checked and marked with no `await` between.
        self._releasing: set[str] = set()
        # The block's `gh pr checks` and `gh release view` answers, for a board read.
        self.details = Asked()

    def _release_records(self, journal: Journal | None, key: str) -> list[dict[str, Any]]:
        if journal is None:
            return []
        try:
            return journal.records(key, kind="release")
        except Busy:
            return []

    async def _release_facts(
        self,
        root: Path,
        units_: list[dict[str, Any]],
        prs: PrsOnce,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """What git and `gh` say now, and the state `release.classify` makes of it.

        Reads only, against the `origin/main` and tags the last fetch brought. A block whose `state`
        is `unknown` or `nothing` carries only its reason. `prs` is awaited only once a release tag
        is found, so a workspace never released asks `gh` nothing here.
        """
        try:
            origin = await gitops.rev_parse(root, "refs/remotes/origin/main")
            tags = await gitops.release_tags(root, origin)
        except GitError as e:
            return _empty_block("unknown", str(e))
        last_tag = ""
        for tag in release.candidates(tags):
            code, said = await release.cos(root, "check-tag", tag)
            if code == 0 and said.strip() == "release":
                last_tag = tag
                break
        if not last_tag:
            return _empty_block("nothing", "no release yet")
        got = await prs()
        if isinstance(got, str):
            return {**_empty_block("unknown", got), "last_tag": last_tag}
        open_prs = release.release_prs(got)
        try:
            tag_sha = await gitops.rev_parse(root, f"refs/tags/{last_tag}")
            commits = await gitops.commits_between(root, tag_sha, origin)
            main_version = str(
                tomllib.loads(await gitops.show_file(root, origin, "pyproject.toml"))
                .get("project", {})
                .get("version")
                or ""
            )
        except (GitError, tomllib.TOMLDecodeError) as e:
            return {**_empty_block("unknown", str(e)), "last_tag": last_tag}
        matched = release.match_commits(commits, units_)
        verdict = release.classify(
            last_tag=last_tag,
            units=matched["units"],
            unmatched=matched["unmatched"],
            open_pr=open_prs[0] if open_prs else None,
            main_version=main_version,
            tags=tags,
            last_record=records[-1] if records else None,
        )
        block = {
            **_empty_block(verdict["state"], verdict["reason"]),
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
        `gh`, through `details`: waited on the first time a pull request's head or a tag is asked,
        or when `fresh`; else held and asked again in the background.
        """
        state = block["state"]
        block["button"] = release.BUTTON.get(state, "")
        block["enabled"] = bool(block["button"])
        if state == "pr-open":
            open_pr = release.release_prs(block.pop("_prs", []) or [])
            row = open_pr[0] if open_pr else {}
            number = row.get("number")
            block["pr"] = number
            block["head"] = str(row.get("headRefOid") or "")

            async def _ask_checks(number: Any) -> list[dict[str, Any]] | str:
                try:
                    return await integrate.required_checks(str(root), int(number))
                except (integrate.IntegrateError, TypeError, ValueError) as e:
                    return str(e)

            checks: list[dict[str, Any]] | str = (
                "no open release pull request"
                if number is None
                else await self.details.get(
                    (str(root), "checks", str(number), block["head"]),
                    lambda: _ask_checks(number),
                    fresh,
                )
            )
            block["checks"] = checks if isinstance(checks, list) else []
            opened, _ = release.opened_head(records, block["version"])
            why = release.publish_problem(
                checks,
                block["head"],
                opened,
                f"v{block['version']}" in block.get("tags", []),
                block["version"],
            )
            block["enabled"], block["disabled_reason"] = not why, why
        elif state == "tagged":
            tag = block["last_tag"]
            status = await self.details.get(
                (str(root), "status", tag), lambda: release.release_status(str(root), tag), fresh
            )
            block.update(status)
            if status["release"] == "published":
                block["state"], block["reason"] = "published", f"{block['last_tag']} is published"

    async def attach_release(
        self,
        cwd: str,
        units_: list[dict[str, Any]],
        journal: Journal | None,
        key: str,
        prs: PrsOnce,
        fresh: bool = False,
    ) -> dict[str, Any] | None:
        """The board's `release` block, or None for a workspace that is not a git checkout.

        Costs, at most: `git` reads, one `coscc.loop check-tag` per candidate tag, and once a
        release tag is found the board's shared `gh pr list`, then `gh pr checks` on an open release
        pull request or `gh release view` and `gh run list` after a tag this app pushed; each `gh`
        answer is held, and waited on only the first time or when `fresh`. No fetch.
        """
        root = Path(cwd).expanduser().resolve()
        if not (root / ".git").exists():
            return None
        records = self._release_records(journal, key)
        block = await self._release_facts(root, units_, prs, records)
        await self._release_detail(root, block, records, fresh)
        for k in ("_prs", "tags", "origin_sha"):
            block.pop(k, None)
        return block

    def _release_start(self, cwd: str) -> tuple[Journal, str, Path]:
        self.ws.check(cwd)
        refuse_while_updating(self.updater)
        journal = self.ws.journal()
        if journal is None:
            raise Invalid(
                "no working folder is set, so a release cannot be recorded — set COS_WORKING_DIR"
            )
        return journal, self.ws.key(cwd), Path(cwd).expanduser().resolve()

    def release_tree_path(self, cwd: str) -> Path:
        """`worktrees.release_path` for `cwd`, worked out again on every call: it is the `expected`
        each writing `gitops` function checks the tree it was handed against.
        """
        try:
            return worktrees.release_path(cwd, self.config.data_dir)
        except BadUnit as e:
            raise GitError(str(e)) from e

    async def _release_tree_fresh(self, cwd: str, root: Path, sha: str) -> Path:
        """The release worktree, detached at `sha`; a tree left over is removed first."""
        tree = self.release_tree_path(cwd)
        await self._release_tree_gone(cwd, root, tree)
        tree.parent.mkdir(parents=True, exist_ok=True)
        await gitops.worktree_add(root, tree, sha)
        return tree

    async def _release_tree_gone(self, cwd: str, root: Path, tree: Path) -> None:
        listed = {str(Path(t["path"]).resolve()) for t in await gitops.worktree_list(root)}
        if str(tree.resolve()) in listed:
            await gitops.release_tree_remove(root, tree, self.release_tree_path(cwd))

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
        ctx: dict[str, Any] = {}

        def write(outcome: release.Outcome, **fields: Any) -> dict[str, Any]:
            rec = release.record(
                workspace=key, phase=phase, version=version, outcome=outcome, **{**ctx, **fields}
            )
            try:
                return journal.append(rec)
            except BadRecord, Busy:
                return rec

        # Check-and-mark with no `await` between, as `Holds.take` does.
        if key in self._releasing:
            reason = release.refusal(
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
                await gitops.fetch_with_tags(root)
                data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
            except (GitError, Unavailable) as e:
                write("failed", detail=f"could not read the workspace: {e}")
                raise Invalid(f"could not read the workspace: {e}") from e
            # Asked before the facts, tag or no tag: an open release pull request is a refusal reason.
            # `_release_facts` gets the same answer, not a second call.
            prs_once = open_prs_once(str(root))
            prs = await prs_once()
            records = self._release_records(journal, key)
            facts = await self._release_facts(root, data["units"], prs_once, records)
            ctx.update(
                proposed=facts["proposed"],
                last_tag=facts["last_tag"],
                units=facts["units"],
                commits=facts["unmatched"],
            )
            open_rel = release.release_prs(prs) if isinstance(prs, list) else []
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
                check_version = await release.cos(tree, "check-version")
                code, said = await release.cos(tree, "check-tag", f"v{version}")
                on_remote = False
                if code == 0 and said.strip() == "release":
                    try:
                        on_remote = await gitops.remote_has_tag(tree, f"v{version}")
                    except GitError as e:
                        code, said = 1, f"the remote's tags could not be read: {e}"
                problem = release.version_problem(version, code, said, facts["last_tag"], on_remote)
                if not problem and phase == "publish" and version != facts["version"]:
                    problem = f"the release waiting to be tagged is {facts['version'] or 'none'}, not {version}"
            reason = release.refusal(
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
                try:
                    checks: list[dict[str, Any]] | str = await integrate.required_checks(
                        str(root), int(row["number"])
                    )
                except integrate.IntegrateError as e:
                    checks = str(e)
                opened, _ = release.opened_head(records, version)
                reason = release.publish_problem(
                    checks,
                    str(row.get("headRefOid") or ""),
                    opened,
                    f"v{version}" in facts.get("tags", []),
                    version,
                )
                ctx.update(pr=row.get("number"), head=str(row.get("headRefOid") or ""))
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
        branch = release.branch_name(version)
        cut = False
        head = ""
        try:
            code, said = await release.cos(tree, "check-branch", branch)
            if code != 0:
                # Still before anything changed, so a 400 like every other refusal.
                write("refused", detail=f"check-branch refused {branch}: {said}")
                try:
                    await self._release_tree_gone(cwd, root, tree)
                except GitError:
                    pass
                raise Invalid(f"check-branch refused {branch}: {said}")
            await gitops.create_branch(tree, branch, facts["origin_sha"])
            cut = True
            await release.set_versions(tree, facts["old"], version)
            code, said = await release.cos(tree, "check-version")
            if code != 0 or (said.split() or [""])[0] != version:
                raise release.ReleaseError(f"check-version printed {said!r}, not {version}")
            extra = release.extra_diff(
                await gitops.diff_u0(tree, self.release_tree_path(cwd)), facts["old"], version
            )
            if extra:
                raise release.ReleaseError(
                    "the change is more than the version lines: " + "; ".join(extra)
                )
            head = await gitops.commit_files(
                tree, self.release_tree_path(cwd), f"chore(release): {version}"
            )
            await gitops.push_branch(tree, self.release_tree_path(cwd), branch)
            number = await release.create_pr(
                str(tree),
                branch,
                f"chore(release): {version}",
                release.pr_body(version, facts["units"], facts["unmatched"]),
            )
            await gitops.detach_here(tree, self.release_tree_path(cwd))
            rec = write("opened", pr=number, head=head)
            yield ("done", {"release": rec})
        except (GitError, release.ReleaseError) as e:
            rec = write("failed", detail=str(e))
            try:
                await self._release_tree_gone(cwd, root, tree)
            except GitError:
                pass
            if cut:
                # Only where the app left it: at `origin/main`, or at its own commit that changes the version
                # and nothing else. Left behind, the next Prepare of this version stops at `create_branch`.
                try:
                    await gitops.delete_merged_branch(root, branch, head or facts["origin_sha"])
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
                await release.merge_pr(str(tree), number, head)
                merged = {
                    "pr": number,
                    "head": head,
                    "merge_sha": await release.merge_commit(str(tree), number),
                }
            else:
                found = await release.merged_release_pr(str(root), release.branch_name(version))
                merged = {
                    "pr": found["number"],
                    "head": found["head"],
                    "merge_sha": found["merge_sha"],
                }
            sha = merged["merge_sha"]
            await gitops.fetch_with_tags(root)
            origin = await gitops.rev_parse(root, "refs/remotes/origin/main")
            if not await gitops.is_ancestor(root, sha, origin):
                raise release.ReleaseError(f"the merge commit {sha[:7]} is not on origin/main")
            tree = await self._release_tree_fresh(cwd, root, sha)
            code, said = await release.cos(tree, "check-version")
            if code != 0 or (said.split() or [""])[0] != version:
                raise release.ReleaseError(
                    f"check-version at {sha[:7]} printed {said!r}, not {version}"
                )
            await gitops.push_tag(tree, self.release_tree_path(cwd), tag, sha)
            rec = write("tagged", **merged)
            try:
                await self._release_tree_gone(cwd, root, tree)
                # The tag was made on the remote only; the board reads local tags.
                await gitops.fetch_with_tags(root)
            except GitError:
                pass
            yield ("done", {"release": rec})
        except (
            GitError,
            release.ReleaseError,
            integrate.IntegrateError,
            IndexError,
            KeyError,
        ) as e:
            rec = write("merged" if merged else "failed", detail=str(e), **merged)
            try:
                await self._release_tree_gone(cwd, root, tree)
            except GitError:
                pass
            yield ("done", {"release": rec})
        finally:
            self._releasing.discard(key)
