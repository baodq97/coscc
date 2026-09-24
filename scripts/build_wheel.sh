#!/usr/bin/env bash
# The one recipe for a coscc wheel. `.github/workflows/release.yml` runs it on a tag, and the
# board's *Build local* button runs it in a throwaway worktree of `origin/main`
# (`.cos/0068_updating-the-app-is-a-manual-reinstall` R6): a second copy of these steps
# inside the app would drift from this one unobserved, because nobody runs the app's copy
# on a tag.
#
#   scripts/build_wheel.sh [--out <dir>] [--local]
#
#   --out <dir>  where the wheel lands (default: dist)
#   --local      stamp the version `<version>+g<sha7>` (PEP 440 local version), so a build
#                of `main` is never mistaken for the release carrying the same number.
#                `pyproject.toml` is put back on exit, success or not.
#
# The last line of stdout is the wheel's path. Exit non-zero means no wheel to trust.
#
# A wheel with no `coscc/_web/` in it still builds, still installs, and just has no
# frontend: `uv build` does not know that directory is supposed to exist, and
# `pyproject.toml` declares no package data on purpose — `uv_build` already includes
# everything under `coscc/`, `.gz` sidecars included, measured 2026-09-22 by building a
# wheel with a probe file under `coscc/_web/` and finding it in `unzip -l`. So the copy
# steps below are the only thing that puts the compiled frontend where the wheel will pick
# it up, and `check_wheel.py` at the end is the only thing that would notice if a copy
# silently found nothing to do — see `.cos/0011_no-install-path-on-a-clean-machine/spec.md`
# R13 and `plan.md`, `## Risks` item 4.
set -euo pipefail

out=dist
local_build=0
while [ $# -gt 0 ]; do
  case "$1" in
    --out) out="${2:?--out needs a directory}"; shift 2 ;;
    --local) local_build=1; shift ;;
    *) echo "usage: $0 [--out <dir>] [--local]" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."
commit=$(git rev-parse HEAD)

if [ "$local_build" = 1 ]; then
  # `spike.md ## U2` (0068) measured this exact edit: `uv build --wheel` names the file
  # `coscc-X.Y.Z+g<sha7>-py3-none-any.whl`, the `+` kept, and the installed copy reports
  # the same string from `importlib.metadata.version`.
  cp pyproject.toml pyproject.toml.build-wheel-orig
  trap 'mv -f pyproject.toml.build-wheel-orig pyproject.toml' EXIT
  sed -i -E "0,/^version = \"([^\"+]+)\"/s//version = \"\\1+g${commit:0:7}\"/" pyproject.toml
fi

uv sync --frozen

# Wraps `reflex export --frontend-only --no-zip` (see coscc/build.py) and writes the bundle
# to .web/build/client/. Reflex fetches its own Node/Bun the first time this runs, so
# nothing upstream of this step needs to provision one.
uv run coscc-build >&2

# `.web/` is gitignored and never committed (see .gitignore), so this copy is the only thing
# that puts the bundle somewhere `uv build` will include it — `pyproject.toml` declares
# `module-name = "coscc"`, so only what lands under `coscc/` travels in the wheel (0011
# spec.md R1).
# The `build/client` segment is preserved deliberately, not out of habit.
# `REFLEX_WEB_WORKDIR` names a *web directory*, and Reflex's static mount appends
# `Dirs.STATIC` — `build/client` — to whatever it names (read from
# reflex/utils/exec.py:376-380, 2026-09-22). Flattening the copy here would leave
# `coscc/frontend.py` pointing that variable at a tree whose `build/client` does not exist,
# and the page would 404 while the API stayed healthy.
rm -rf coscc/_web
mkdir -p coscc/_web/build/client
cp -r .web/build/client/. coscc/_web/build/client/
# `.web/backend/` is not part of the static bundle and is easy to read as build scratch. It
# is not. `stateful_pages.json` is the marker that lets Reflex take its short path when the
# compile is skipped; without it, `compile_app` falls through to a full compile and ends on
# `Bun or npm not found` (reflex/compiler/compiler.py:1254-1267, read 2026-09-22). Measured
# on a clean Debian 13 VM the same day: a wheel carrying only `build/client` installs,
# reports `active`, and serves nothing at all.
mkdir -p coscc/_web/backend
cp -r .web/backend/. coscc/_web/backend/

# The second thing this app reads from outside `coscc/`, and the one that shipped missing
# three times. `.cos/0012_installed-copy-runs-no-stage/intent.md` measured v0.2.2: the
# Board answered 400 and every step ran with no rules in its prompt, because
# `coscc/board.py` and `coscc/runner.py` were reaching for a `.claude/` that only exists in
# a checkout.
#
# Two named directories, never `.claude/` whole: `.claude/settings.local.json` is a
# personal file (`.gitignore`) and a release is published. `coscc/harness.py` refuses a
# wheel that carries one. `rm -rf` first: a tree left over from an earlier build would be
# copied into the wheel alongside the new one, and the packaged copy is the one that wins.
rm -rf coscc/_harness
mkdir -p coscc/_harness
cp -r .claude/scripts coscc/_harness/scripts
cp -r .claude/skills coscc/_harness/skills

# The build stamp the board reads its commit from (0068 R1). Always written, never read
# back by this script: a stale one left in a checkout is overwritten here.
printf '{"commit": "%s"}\n' "$commit" > coscc/_build.json

mkdir -p "$out"
uv build --wheel --out-dir "$out" >&2
# The newest wheel is this one: `uv build` names it from the version, and an older wheel in
# `$out` of the same name is overwritten, which also makes it the newest.
wheel=$(ls -t "$out"/*.whl | head -n 1)

# Without the copy steps above, `uv build` still succeeds and the wheel still installs —
# it is only missing something. This is the check that would notice; the decision lives in
# `coscc.harness.wheel_complaints` (`.cos/0012_installed-copy-runs-no-stage/spec.md` C1).
uv run python scripts/check_wheel.py "$wheel" >&2

echo "$wheel"
