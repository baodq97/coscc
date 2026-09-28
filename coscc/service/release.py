"""Cutting a release: the board's *Release* block and its two presses (`0046`).

A mixin with no fields of its own, like the others `Service` inherits; `_releasing` is on
`Service`. `coscc/github/release.py` holds the pure decisions and the `gh`/`node`/`uv` calls,
`coscc/git/gitops.py` the git ones.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from coscc.github import integrate, release
from coscc.git import gitops, worktrees
from coscc.git.gitops import GitError
from coscc.runlog.journal import BadRecord, Busy, Journal
from coscc.units import BadUnit
from coscc.units import board as board_reader
from coscc.units.board import Unavailable
from coscc.service.common import CONSEQUENCE, Invalid

PrsOnce = Callable[[], Awaitable["list[dict[str, Any]] | str"]]


def _empty_block(state: str, reason: str) -> dict[str, Any]:
    return {
        "state": state, "reason": reason, "last_tag": "", "units": [], "unmatched": [], "count": 0,
        "proposed": "", "version": "", "pr": None, "checks": [], "head": "", "button": "",
        "enabled": False, "disabled_reason": "", "warning": release.WARNING,
        "consequence": CONSEQUENCE["release"], "release_url": "", "workflow": "", "workflow_url": "",
    }


class ReleaseMixin:

    # -- reading (R1, R4, R9, R11, R12) ---------------------------------------

    def _release_records(self, journal: Journal | None, key: str) -> list[dict[str, Any]]:
        if journal is None:
            return []
        try:
            return journal.records(key, kind="release")
        except Busy:
            return []

    async def _release_facts(
        self, root: Path, units_: list[dict[str, Any]], prs: PrsOnce, records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """What git and `gh` say now, and the state `release.classify` makes of it.

        Reads only, against the `origin/main` and tags the last fetch brought. A block whose
        `state` is `unknown` or `nothing` carries only its reason. `prs` is awaited only once
        a release tag is found, so a workspace never released asks `gh` nothing here."""
        if not (root / release.SCRIPT).is_file():
            return _empty_block("unknown", "this workspace has no cos.mjs, so it is not released from here")
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
            main_version = str(tomllib.loads(await gitops.show_file(root, origin, "pyproject.toml"))
                               .get("project", {}).get("version") or "")
        except (GitError, tomllib.TOMLDecodeError) as e:
            return {**_empty_block("unknown", str(e)), "last_tag": last_tag}
        matched = release.match_commits(commits, units_)
        verdict = release.classify(
            last_tag=last_tag, units=matched["units"], unmatched=matched["unmatched"],
            open_pr=open_prs[0] if open_prs else None, main_version=main_version, tags=tags,
            last_record=records[-1] if records else None,
        )
        block = {
            **_empty_block(verdict["state"], verdict["reason"]),
            "last_tag": last_tag, "units": matched["units"], "unmatched": matched["unmatched"],
            "count": len(matched["units"]), "version": verdict["version"],
            "proposed": verdict["version"] if verdict["state"] == "ready" else "",
            "main_version": main_version, "origin_sha": origin, "tags": tags, "_prs": got,
        }
        return block

    async def _release_detail(self, root: Path, block: dict[str, Any], records: list[dict[str, Any]]) -> None:
        """The button, whether it may be pressed, and R11's workflow. Only the state that
        needs one asks `gh` again."""
        state = block["state"]
        block["button"] = release.BUTTON.get(state, "")
        block["enabled"] = bool(block["button"])
        if state == "pr-open":
            open_pr = release.release_prs(block.pop("_prs", []) or [])
            row = open_pr[0] if open_pr else {}
            block["pr"] = row.get("number")
            block["head"] = str(row.get("headRefOid") or "")
            try:
                checks: list[dict[str, Any]] | str = await integrate.required_checks(str(root), int(block["pr"]))
            except (integrate.IntegrateError, TypeError, ValueError) as e:
                checks = str(e)
            block["checks"] = checks if isinstance(checks, list) else []
            opened, _ = release.opened_head(records, block["version"])
            why = release.publish_problem(
                checks, block["head"], opened, f"v{block['version']}" in block.get("tags", []), block["version"])
            block["enabled"], block["disabled_reason"] = not why, why
        elif state == "tagged":
            status = await release.release_status(str(root), block["last_tag"])
            block.update(status)
            if status["release"] == "published":
                block["state"], block["reason"] = "published", f"{block['last_tag']} is published"

    async def _attach_release(
        self, cwd: str, units_: list[dict[str, Any]], journal: Journal | None, key: str, prs: PrsOnce,
    ) -> dict[str, Any] | None:
        """R1: the board's `release` block, or None for a workspace that is not a git checkout.

        Costs, at most: `git` reads, one `node cos.mjs check-tag` per candidate tag, and
        once a release tag is found the board's shared `gh pr list`, then `gh pr checks` on
        an open release pull request or `gh release view` and `gh run list` after a tag this
        app pushed. No fetch."""
        root = Path(cwd).expanduser().resolve()
        if not (root / ".git").exists():
            return None
        records = self._release_records(journal, key)
        block = await self._release_facts(root, units_, prs, records)
        await self._release_detail(root, block, records)
        for k in ("_prs", "tags", "origin_sha"):
            block.pop(k, None)
        return block

    # -- pressing (R5, R6, R7, R10, R13, R15) -----------------------------------

    def _release_start(self, cwd: str) -> tuple[Journal, str, Path]:
        self._workspace_or_refuse(cwd)
        self._refuse_while_updating()
        journal = self._journal()
        if journal is None:
            raise Invalid("no working folder is set, so a release cannot be recorded — set COS_WORKING_DIR")
        return journal, self._journal_key(cwd), Path(cwd).expanduser().resolve()

    def _release_tree_path(self, cwd: str) -> Path:
        """`worktrees.release_path` for `cwd`, worked out again on every call: it is the
        `expected` each writing `gitops` function checks the tree it was handed against."""
        try:
            return worktrees.release_path(cwd, self.config.data_dir)
        except BadUnit as e:
            raise GitError(str(e)) from e

    async def _release_tree_fresh(self, cwd: str, root: Path, sha: str) -> Path:
        """The release worktree, detached at `sha`; a tree left from before is removed first."""
        tree = self._release_tree_path(cwd)
        await self._release_tree_gone(cwd, root, tree)
        tree.parent.mkdir(parents=True, exist_ok=True)
        await gitops.worktree_add(root, tree, sha)
        return tree

    async def _release_tree_gone(self, cwd: str, root: Path, tree: Path) -> None:
        listed = {str(Path(t["path"]).resolve()) for t in await gitops.worktree_list(root)}
        if str(tree.resolve()) in listed:
            await gitops.release_tree_remove(root, tree, self._release_tree_path(cwd))

    async def _release_press(
        self, cwd: str, phase: str, version: str,
    ) -> tuple[Journal, str, Path, dict[str, Any], Path, str, Callable[..., dict[str, Any]]]:
        """Everything R13 asks before a press changes anything: the fetch, the facts, the
        release tree at `origin/main` and `cos.mjs` there. Refuses with one `refused` record."""
        journal, key, root = self._release_start(cwd)
        version = str(version or "").strip()
        ctx: dict[str, Any] = {}

        def write(outcome: str, **fields: Any) -> dict[str, Any]:
            rec = release.record(workspace=key, phase=phase, version=version, outcome=outcome, **{**ctx, **fields})
            try:
                return journal.append(rec)
            except (BadRecord, Busy):
                return rec

        # Check-and-mark with no `await` between, as `_take` does (R13, first reason).
        if key in self._releasing:
            reason = release.refusal(active=True, phase=phase, open_release_pr=None, has_script=True,
                                     check_version=(0, ""), version_problem_="", state="")
            write("refused", detail=reason)
            raise Invalid(reason)
        self._releasing.add(key)
        try:
            try:
                await gitops.fetch_with_tags(root)
                data = await board_reader.read(self._units_root(cwd), peers=self._peers())
            except (GitError, Unavailable) as e:
                write("failed", detail=f"could not read the workspace: {e}")
                raise Invalid(f"could not read the workspace: {e}") from e
            # Asked before the facts, tag or no tag: an open release pull request is R13's
            # second reason. `_release_facts` gets the same answer, not a second call.
            prs_once = self._prs_once(str(root))
            prs = await prs_once()
            records = self._release_records(journal, key)
            facts = await self._release_facts(root, data["units"], prs_once, records)
            ctx.update(proposed=facts["proposed"], last_tag=facts["last_tag"],
                       units=facts["units"], commits=facts["unmatched"])
            open_rel = release.release_prs(prs) if isinstance(prs, list) else []
            has_script = (root / release.SCRIPT).is_file()
            tree: Path | None = None
            check_version = (1, facts["reason"] if facts["state"] == "unknown" else "")
            problem = ""
            if has_script and facts["state"] != "unknown":
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
                active=False, phase=phase, open_release_pr=open_rel[0] if open_rel else None,
                has_script=has_script, check_version=check_version, version_problem_=problem,
                state=facts["state"],
            )
            checked: dict[str, Any] = {}
            if not reason and facts["state"] == "pr-open":
                row = open_rel[0]
                try:
                    checks: list[dict[str, Any]] | str = await integrate.required_checks(str(root), int(row["number"]))
                except integrate.IntegrateError as e:
                    checks = str(e)
                opened, _ = release.opened_head(records, version)
                reason = release.publish_problem(
                    checks, str(row.get("headRefOid") or ""), opened, f"v{version}" in facts.get("tags", []), version)
                ctx.update(pr=row.get("number"), head=str(row.get("headRefOid") or ""))
                # R10.1: the merge is pinned to the head R9 just passed, which is the one
                # the app pushed; the pull request is not read a second time.
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
        """R6, the first press: a `chore/release-X-Y-Z` pull request that changes the four
        version files and nothing else. One `release` record whatever happens (R15)."""
        journal, key, root, facts, tree, version, write = await self._release_press(cwd, "prepare", version)
        branch = release.branch_name(version)
        cut = False
        head = ""
        try:
            code, said = await release.cos(tree, "check-branch", branch)
            if code != 0:
                # R6.3: still before anything changed, so a 400 like every other refusal.
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
                await gitops.diff_u0(tree, self._release_tree_path(cwd)), facts["old"], version)
            if extra:
                raise release.ReleaseError("the change is more than the version lines: " + "; ".join(extra))
            head = await gitops.commit_files(tree, self._release_tree_path(cwd), f"chore(release): {version}")
            await gitops.push_branch(tree, self._release_tree_path(cwd), branch)
            number = await release.create_pr(
                str(tree), branch, f"chore(release): {version}",
                release.pr_body(version, facts["units"], facts["unmatched"]))
            await gitops.detach_here(tree, self._release_tree_path(cwd))
            rec = write("opened", pr=number, head=head)
            yield ("done", {"release": rec})
        except (GitError, release.ReleaseError) as e:
            rec = write("failed", detail=str(e))
            try:
                await self._release_tree_gone(cwd, root, tree)
            except GitError:
                pass
            if cut:
                # Only where the app left it: at `origin/main`, or at its own commit that
                # changes the version and nothing else. Left behind, the next Prepare of
                # this version stops at `create_branch` (review F2).
                try:
                    await gitops.delete_merged_branch(root, branch, head or facts["origin_sha"])
                except GitError:
                    pass
            yield ("done", {"release": rec})
        finally:
            self._releasing.discard(key)

    async def release_publish(self, cwd: str, version: str) -> AsyncIterator[tuple[str, Any]]:
        """R10, the second press: merge the release pull request, then tag its merge commit.
        From `merged-untagged` it starts at the tag (R12). Never started by anything but a
        request (R14)."""
        journal, key, root, facts, tree, version, write = await self._release_press(cwd, "publish", version)
        tag = f"v{version}"
        merged: dict[str, Any] = {}
        try:
            if facts["state"] == "pr-open":
                number, head = facts["checked_pr"], facts["checked_head"]
                await release.merge_pr(str(tree), number, head)
                merged = {"pr": number, "head": head, "merge_sha": await release.merge_commit(str(tree), number)}
            else:
                found = await release.merged_release_pr(str(root), release.branch_name(version))
                merged = {"pr": found["number"], "head": found["head"], "merge_sha": found["merge_sha"]}
            sha = merged["merge_sha"]
            await gitops.fetch_with_tags(root)
            origin = await gitops.rev_parse(root, "refs/remotes/origin/main")
            if not await gitops.is_ancestor(root, sha, origin):
                raise release.ReleaseError(f"the merge commit {sha[:7]} is not on origin/main")
            tree = await self._release_tree_fresh(cwd, root, sha)
            code, said = await release.cos(tree, "check-version")
            if code != 0 or (said.split() or [""])[0] != version:
                raise release.ReleaseError(f"check-version at {sha[:7]} printed {said!r}, not {version}")
            await gitops.push_tag(tree, self._release_tree_path(cwd), tag, sha)
            rec = write("tagged", **merged)
            try:
                await self._release_tree_gone(cwd, root, tree)
                # The tag was made on the remote only; the board reads local tags.
                await gitops.fetch_with_tags(root)
            except GitError:
                pass
            yield ("done", {"release": rec})
        except (GitError, release.ReleaseError, integrate.IntegrateError, IndexError, KeyError) as e:
            rec = write("merged" if merged else "failed", detail=str(e), **merged)
            try:
                await self._release_tree_gone(cwd, root, tree)
            except GitError:
                pass
            yield ("done", {"release": rec})
        finally:
            self._releasing.discard(key)
