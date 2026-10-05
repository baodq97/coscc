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
# `pyproject.toml` declares no package data: `uv_build` includes everything under `coscc/`,
# so the steps below put what the app reads there, and `check_wheel.py` at the end is what
# notices a step that found nothing to do.
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

# The studio (`ui/`, served by `coscc/http/studio.py`) builds straight into `coscc/_studio/`, inside
# the package, so `uv build` carries it. `npm ci` installs exactly the lock file.
npm --prefix ui ci --no-audit --no-fund >&2
npm --prefix ui run build >&2

# The one thing this app reads from outside `coscc/`, and the one that shipped missing
# three times. `.cos/0012_installed-copy-runs-no-stage/intent.md` measured v0.2.2: the
# Board answered 400 and every step ran with no rules in its prompt, because
# `coscc/units/board.py` and `coscc/runner/step.py` were reaching for a `.claude/` that only exists in
# a checkout.
#
# One named directory, never `.claude/` whole (the loop is `coscc/loop/`, in the package already):
# `.claude/settings.local.json` is a personal file (`.gitignore`) and a release is published. `coscc/agent/harness.py` refuses a
# wheel that carries one. `rm -rf` first: a tree left over from an earlier build would be
# copied into the wheel alongside the new one, and the packaged copy is the one that wins.
rm -rf coscc/_harness
mkdir -p coscc/_harness
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
# `coscc.agent.harness.wheel_complaints` (`.cos/0012_installed-copy-runs-no-stage/spec.md` C1).
uv run python scripts/check_wheel.py "$wheel" >&2

echo "$wheel"
