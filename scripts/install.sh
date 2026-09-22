#!/bin/sh
# coscc installer and updater.
#
#     curl -LsSf https://github.com/baodq97/coscc/releases/latest/download/install.sh | sh
#
# See docs/install.md for what this does and why, and for what it deliberately does not do
# (no auth, no uninstall script).
#
# This file, scripts/install.sh, is a TEMPLATE. `.github/workflows/release.yml` substitutes
# the two placeholders below at release time and attaches the result to each GitHub release
# under the stable name `install.sh`; that substituted copy is what the command above
# fetches. Running this template unsubstituted refuses on purpose -- see the check right
# after `set -eu`.
#
# Order of operations, and why it cannot be reordered, is
# .cos/0011_no-install-path-on-a-clean-machine/plan.md step 8 / spec.md R9-R12:
#   1. refuse unless this machine is Linux, booted with systemd
#   2. install uv, if it is not already here
#   3. download the wheel for the version baked into this script
#   4. verify its sha256 against the value baked into this script -- refuse on any mismatch
#   5. `uv tool install --force` the wheel
#   6. write the env file only if it does not exist yet -- an update must never touch it
#      (plan.md OQ3, Risks item 7: losing COS_WORKING_DIR looks like data loss)
#   7. write the systemd user unit -- rewritten every run, since it holds no data of the
#      user's, unlike the env file next to it
#   8. `loginctl enable-linger` for the invoking user, so the unit can start at boot before
#      anyone has logged in
#   9. enable and (re)start the unit
#
# Re-running this exact script is the only supported update path (spec.md C5): the wheel
# filename carries the version number, so `/releases/latest/download/` cannot serve it
# under a name `uv tool upgrade coscc` would ever find on its own.

set -eu

COSCC_VERSION="@@COSCC_VERSION@@"
COSCC_WHEEL_SHA256="@@COSCC_WHEEL_SHA256@@"

case "$COSCC_VERSION" in
  @@*)
    echo "install.sh: this is the unsubstituted template from the coscc repository, not a" >&2
    echo "release asset -- it has no version or checksum baked in yet. Run the copy" >&2
    echo "attached to a GitHub release instead:" >&2
    echo "  curl -LsSf https://github.com/baodq97/coscc/releases/latest/download/install.sh | sh" >&2
    exit 1
    ;;
esac

# ---------------------------------------------------------------------------------------
# Options: spec.md's pinned surface is --port, --host, --working-dir. Piped into `sh`,
# this script has no other stdin to read flags from, so they go after `sh -s --`:
#   curl -LsSf .../install.sh | sh -s -- --port 9090 --host 127.0.0.1 --working-dir ~/proj
# ---------------------------------------------------------------------------------------

opt_host="0.0.0.0"
opt_port="8790"
opt_working_dir=""

while [ $# -gt 0 ]; do
  case "$1" in
    --host)
      [ $# -ge 2 ] || { echo "install.sh: --host needs a value" >&2; exit 1; }
      opt_host=$2
      shift 2
      ;;
    --host=*)
      opt_host=${1#--host=}
      shift
      ;;
    --port)
      [ $# -ge 2 ] || { echo "install.sh: --port needs a value" >&2; exit 1; }
      opt_port=$2
      shift 2
      ;;
    --port=*)
      opt_port=${1#--port=}
      shift
      ;;
    --working-dir)
      [ $# -ge 2 ] || { echo "install.sh: --working-dir needs a value" >&2; exit 1; }
      opt_working_dir=$2
      shift 2
      ;;
    --working-dir=*)
      opt_working_dir=${1#--working-dir=}
      shift
      ;;
    *)
      echo "install.sh: unknown option '$1' (known: --host, --port, --working-dir)" >&2
      exit 1
      ;;
  esac
done

case "$opt_port" in
  ''|*[!0-9]*)
    echo "install.sh: --port must be a positive integer, got '$opt_port'" >&2
    exit 1
    ;;
esac

# ---------------------------------------------------------------------------------------
# 1. Linux with systemd, or refuse politely. intent.md C1: everything else is out of
# scope, and docs/install.md has to say so rather than this script failing unexplained.
# ---------------------------------------------------------------------------------------

os_name=$(uname -s)
if [ "$os_name" != "Linux" ]; then
  echo "install.sh: coscc installs on Linux with systemd only; this machine reports" >&2
  echo "'$os_name'. See docs/install.md." >&2
  exit 1
fi

if [ ! -d /run/systemd/system ] || ! command -v systemctl >/dev/null 2>&1; then
  echo "install.sh: coscc needs systemd running as this machine's init, and 'systemctl'" >&2
  echo "on PATH; at least one of those was not found. See docs/install.md." >&2
  exit 1
fi

# ---------------------------------------------------------------------------------------
# 2. uv, if it is not here already.
# ---------------------------------------------------------------------------------------

# `command -v` alone is not the same question as "is uv here". uv installs itself to
# $HOME/.local/bin, and a non-login shell -- which is what `ssh host 'sh install.sh'` and
# most automation give you -- does not have that on PATH: measured 2026-09-22 on Debian 13,
# where PATH was `/usr/local/bin:/usr/bin:/bin:/usr/games` and an existing uv was invisible.
# Looking only at PATH therefore re-downloads ~50MB of uv on every update run, and makes
# the "if it is not already here" in docs/install.md untrue.
if ! command -v uv >/dev/null 2>&1; then
  for candidate in "${UV_INSTALL_DIR:-}" "${XDG_BIN_HOME:-}" "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    if [ -n "$candidate" ] && [ -x "$candidate/uv" ]; then
      PATH="$candidate:$PATH"
      export PATH
      break
    fi
  done
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "install.sh: uv not found, installing it"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
  export PATH
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "install.sh: uv's installer ran but 'uv' is still not on PATH; nothing past this" >&2
  echo "point can proceed without it. Open a new shell and re-run this script." >&2
  exit 1
fi

# ---------------------------------------------------------------------------------------
# 3-4. The wheel for this exact version, sha256-checked against the value baked above.
# ---------------------------------------------------------------------------------------

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

wheel_name="coscc-${COSCC_VERSION}-py3-none-any.whl"
# The base is overridable so this script can be exercised against a locally built wheel
# before any release carries one -- plan.md step 8's check. It does not weaken the
# integrity check one bit: the sha256 below is still compared against the value baked into
# this file at release time, so a wheel fetched from anywhere else still has to be the
# byte-for-byte wheel that release built.
wheel_url="${COSCC_DOWNLOAD_BASE:-https://github.com/baodq97/coscc}/releases/download/v${COSCC_VERSION}/${wheel_name}"
wheel_path="$work_dir/$wheel_name"

echo "install.sh: downloading $wheel_name"
curl -LsSf -o "$wheel_path" "$wheel_url"

actual_sha256=$(sha256sum "$wheel_path" | awk '{print $1}')
if [ "$actual_sha256" != "$COSCC_WHEEL_SHA256" ]; then
  echo "install.sh: sha256 mismatch for $wheel_name -- refusing to install it" >&2
  echo "  expected: $COSCC_WHEEL_SHA256" >&2
  echo "  got:      $actual_sha256" >&2
  exit 1
fi
echo "install.sh: sha256 verified"

# ---------------------------------------------------------------------------------------
# 5. Install (first run) or reinstall (update run) the wheel.
# ---------------------------------------------------------------------------------------

uv tool install --force "$wheel_path"

bin_dir=${UV_TOOL_BIN_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}
coscc_bin="$bin_dir/coscc"
if [ ! -x "$coscc_bin" ]; then
  echo "install.sh: expected an executable at $coscc_bin after 'uv tool install' and did" >&2
  echo "not find one." >&2
  exit 1
fi

# Best-effort convenience for an interactive shell opened later; the systemd unit below
# never relies on PATH, it calls $coscc_bin by its full path.
uv tool update-shell || true

# ---------------------------------------------------------------------------------------
# 6. The env file: written once, on first install, and never touched again.
# Path is plan.md OQ2: ${XDG_CONFIG_HOME:-$HOME/.config}/coscc/env -- not under
# COS_DATA_DIR, because a file that configures COS_DATA_DIR cannot live inside the
# directory COS_DATA_DIR names (plan.md OQ2's own reasoning).
# ---------------------------------------------------------------------------------------

config_home=${XDG_CONFIG_HOME:-$HOME/.config}
env_dir="$config_home/coscc"
env_file="$env_dir/env"
mkdir -p "$env_dir"

if [ -e "$env_file" ]; then
  echo "install.sh: $env_file already exists, leaving it untouched"
else
  echo "install.sh: writing $env_file"
  {
    echo "# Read by coscc/config.py:from_env -- the only place this app reads the"
    echo "# environment (.cos/0011_no-install-path-on-a-clean-machine/spec.md R11)."
    echo "# install.sh writes this file once, on first install, and never overwrites it"
    echo "# again. Edit it by hand, then apply the change with:"
    echo "#   systemctl --user restart coscc"
    echo "#"
    echo "# COS_HOST is spelled out explicitly here, even though it matches install.sh's"
    echo "# own default, so that a later default change in install.sh can never widen"
    echo "# what an already-installed copy exposes on the network without somebody"
    echo "# editing this line on purpose."
    echo "# (.cos/0011_no-install-path-on-a-clean-machine/plan.md, Risks item 2)"
    echo "COS_HOST=$opt_host"
    echo "COS_PORT=$opt_port"
    if [ -n "$opt_working_dir" ]; then
      echo "COS_WORKING_DIR=$opt_working_dir"
    else
      echo "# COS_WORKING_DIR=/path/to/your/projects"
    fi
  } > "$env_file"
fi

if [ -n "$opt_working_dir" ]; then
  mkdir -p "$opt_working_dir"
fi

# ---------------------------------------------------------------------------------------
# 7. The systemd user unit: rewritten every run. It is generated, not user data -- that
# separation from the env file above is deliberate (plan.md, "Files that change").
# ---------------------------------------------------------------------------------------

unit_dir="$config_home/systemd/user"
unit_file="$unit_dir/coscc.service"
mkdir -p "$unit_dir"

cat > "$unit_file" <<EOF
# Generated by install.sh. Rewritten on every install and update; unlike the env file
# beside it, nothing here was typed by a person, so overwriting it costs nothing.
[Unit]
Description=coscc -- CoS Studio
After=network.target

[Service]
Type=simple
ExecStart=$coscc_bin
EnvironmentFile=$env_file
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
EOF

# ---------------------------------------------------------------------------------------
# 8. Linger: the mechanism that lets the unit start at boot before anyone logs in
# (spec.md R10). This is system state that outlives the install -- there is no matching
# "disable" in this script, see docs/install.md.
# ---------------------------------------------------------------------------------------

loginctl enable-linger "$(id -un)"

# ---------------------------------------------------------------------------------------
# 9. Enable, then restart rather than `enable --now`: on an update, the wheel and the
# unit file just changed underneath a unit that may already be active, and `--now` only
# starts a unit that is not already running -- it would leave the old code serving
# requests. `restart` starts a stopped unit exactly the same way `start` would, so this
# one command covers the first install and every update after it.
# ---------------------------------------------------------------------------------------

systemctl --user daemon-reload
systemctl --user enable coscc
systemctl --user restart coscc

# ---------------------------------------------------------------------------------------
# 10. Wait until it actually answers.
#
# `Type=simple` means systemd calls the unit started the moment the process is spawned,
# and this app takes a few seconds after that to compile its state and bind. Measured
# 2026-09-22 on a real install: about four seconds. Without this wait the script prints
# "running" and returns, the person follows docs/install.md straight to the browser, and
# gets a refused connection on a machine where nothing is wrong -- which is indistinguishable
# from a broken install to someone who has just met this program.
# ---------------------------------------------------------------------------------------

served_host=$(sed -n 's/^COS_HOST=//p' "$env_file" | tail -n1)
served_port=$(sed -n 's/^COS_PORT=//p' "$env_file" | tail -n1)
: "${served_host:=$opt_host}"
: "${served_port:=$opt_port}"

# 0.0.0.0 is a bind address, not somewhere to connect to; reach it over loopback.
probe_host=$served_host
[ "$probe_host" = "0.0.0.0" ] && probe_host=127.0.0.1

printf 'install.sh: waiting for coscc to answer on %s:%s' "$probe_host" "$served_port"
waited=0
while [ "$waited" -lt 90 ]; do
  if curl -sf -o /dev/null --max-time 2 "http://$probe_host:$served_port/api/health"; then
    printf '\n'
    echo "install.sh: coscc $COSCC_VERSION installed and running"
    echo "install.sh: http://$probe_host:$served_port/"
    # Almost nobody installs this on the machine they will browse from -- the supported
    # shape is a VM reached over SSH, where the loopback address printed above is true and
    # useless. When the bind address is every interface, name one the person can actually
    # type, and say what that means rather than leaving it to be discovered.
    if [ "$served_host" = "0.0.0.0" ]; then
      lan=$(ip -4 -o route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')
      # An `&&` list here would be the last word of a `set -e` shell on a host with no
      # default route: the test fails, the list returns 1, and the script exits 1 having
      # just installed successfully.
      if [ -n "$lan" ]; then
        echo "install.sh: http://$lan:$served_port/   (from another machine)"
      fi
      echo "install.sh: bound to 0.0.0.0 -- reachable on every interface of this host, and"
      echo "install.sh: this app has no authentication. See docs/install.md before exposing it."
    fi
    exit 0
  fi
  if [ "$(systemctl --user is-active coscc)" = "failed" ]; then
    printf '\n'
    echo "install.sh: coscc failed to start. What it said:" >&2
    journalctl --user -u coscc -n 30 --no-pager >&2 || true
    exit 1
  fi
  printf '.'
  sleep 1
  waited=$((waited + 1))
done

printf '\n'
echo "install.sh: coscc did not answer within ${waited}s. It may still be starting." >&2
echo "install.sh: check with  systemctl --user status coscc" >&2
echo "install.sh:             journalctl --user -u coscc -n 50" >&2
exit 1
