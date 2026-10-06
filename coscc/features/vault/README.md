# Vault: secrets an agent can use and never read

Read this before changing `coscc/features/vault/`: the tools, the guard, the prompt block and the
routes are all in it. The studio's *Vault* page and the unit line are `ui/index.tsx`. The store, the filter, the scan and the runner are the
package `coscc/vault/`'s; the file has the tables' statements through `vault.TABLES`.

## What it does

- A person keeps a secret on the studio's *Vault* page, in one workspace (`ws:<name>`) or for any workspace it is
  granted to (`global:<name>`). The value goes in through a `<textarea>` and one form POST, `/api/vault/secrets`, which answers
  the name and never the value. A password box would not do: a browser strips its line breaks, and a key is many lines.
  The route turns the textarea's CRLF back into LF and drops trailing line breaks; a value of
  several lines keeps one. It is written encrypted with `age` and no route gives it back.
- An agent whose row holds the catalog entry `vault` (`impl`, `spike`) runs one command with a
  secret passed in, through `vault_exec`, when its run's grant names that secret. The output is filtered of the value in the forms `coscc/vault/` lists before the model sees it.
- Every create, replace, delete, grant, revoke and policy change writes one `kind: "vault"` line
  in the run log, as `human:owner`; every use writes one as `agent:<agent>`. Neither carries a
  value or the command with a placeholder filled in.
- Before any action that pushes begins (the PR machine's `pr` and `ship`, an integration), the
  guard `vault-leak` scans the unit's
  transcripts, run-log lines, artifacts and commits (for `ship`, the pull request too) for the
  values of the workspace's secrets. A hit writes one `kind: "vault-leak"` line with the name,
  the place and the form, and holds the step with `feature-refused`. Commits `git log` could not
  read, or a pull request `gh` could not, hold it too: an unscanned unit does not pass.

## The routes

All behind the login; none is in `auth.EXEMPT`. Every one refuses a workspace with the vault off
(the pref `features.off`), except delete and revoke.

- `GET /api/vault/secrets?cwd=`: metadata of the secrets the workspace sees, and of the global
  ones it does not, and whether `age` is installed. Never a value, a length or a hash.
- `POST /api/vault/secrets` (form): the only route that takes a value; `{saved, short}` back. It makes the secret or
  replaces its value. Only `ws:` or `global:` secrets a person makes; a `broker` one takes `ssh`
  and nothing else.
- `POST /api/vault/policy`, `/grant`, `/revoke`, `/delete` (JSON): `{cwd, name, tier}`, and for a
  policy `stages` (the agents that may use it, the page's "Agents that may use it") and `modes`.
- `GET /api/vault/leaks?cwd=&unit=`: the names the last scan of the unit found, for the unit
  page's line.

## What the agent sees

- One catalog entry, `vault` (effect `external`, tier `high`): an MCP server of three tools, made for
  a run whose row names it (`impl`, `spike`). None has a parameter a value could ride in. The
  run's grant names, in `use`, each secret of the workspace with a value whose list of agents
  names the run's agent (`Tool.uses`); nothing else may be used, whatever the call says.
  - `vault_list`: each secret of the workspace (its `ws:` ones and the `global:` ones granted
    to it) with its name, description, tier, ways of passing, `broker` flag, and whether this
    run may use it now.
  - `vault_exec(command, uses, timeout, capture)`: one command in the step's folder (a spike's
    throwaway one). `uses` is `[{name, mode, var}]`, `mode` one of `env`, `file`, `placeholder`,
    `ssh`. It returns the exit code, the filtered `stdout` and `stderr` and how many times each
    secret was masked. With `capture`, stdout is stored as a new `ws:` secret and not returned.
    A secret the grant does not name is refused before anything is read. One secret that may not
    be used means nothing runs, and each is answered with a code:
    `unknown-secret`, `not-granted`, `stage-not-allowed`, `mode-not-allowed`,
    `broker-ssh-only`. The line is read as every session's is (`policy.bash_refused`, the
    critical blocks), and strictly, because it runs with secrets in it: one naming the store, the
    key or another secret, pushing or merging, removing outside the unit's places, or that cannot
    be read or holds a `$()`, backtick or `<()`, is answered `command-refused` with its words. No
    list of programs is kept: `curl`, `psql` and the rest run.
  - `vault_generate(name, description)`: a random `ws:` secret nobody sees. A name that is taken
    is refused, never overwritten.
- One prompt block, `vault`, for a run that holds the entry, but not a step taken up again. A
  secret its grant names shows its name, description and ways of passing; any other shows its
  name only. No value.
- One guard, `vault-leak`, asked before every step; it scans when the run holds a push (the PR
  machine's `pr` and `ship`, Gebo's) and abstains otherwise. It denies with the secrets' names.
- In the studio: a *Vault* sidebar entry, and on an open unit a line naming the secrets found
  in its work, when the guard last held it.

## What it is not

It keeps a secret out of the model's sight and out of what leaves in a pull request. It does not
stop an agent that means to get one.

- **Not a security boundary.** The agent has `python` and `node`. While a command runs it can
  read the `0600` file in the tmpfs folder, `/proc/<pid>/environ` of the child, use the
  `SSH_AUTH_SOCK` of the call, or print a value in an encoding the filter does not catch. Only a
  whole value in one of the listed forms is masked; a part of one is not.
- **One password, one owner.** Whoever holds the login can read no value but can replace, grant
  and delete any; every action is written as `human:owner`.
- **The scan detects, it does not prevent.** A value shown through `Bash` is in the context and
  the transcript before the guard looks. The guard closes the way out to a pull request and to
  the branch an integration pushes; it does not erase a transcript. It runs on the event loop
  (`feature_refusal` calls a guard synchronously), so a long history holds the app while it is
  scanned.
- **Turning it off turns the guard off** (`hooks.on` drops a feature's parts). With the
  vault off for a workspace that still has secrets, a leak from before goes out unchecked.
- **A short value is noisy.** It is masked wherever it appears; the page says so once when a
  value is under 8 bytes.
- **A placeholder is in the command line** and so in `/proc/<pid>/cmdline` while it runs; a new
  secret does not allow `placeholder` by default.
- **Plain HTTP sends a value in the clear**, like the login password. Bind `COS_HOST` to
  loopback, or put HTTPS in front.
- **The store is a copy at rest.** The identity that opens it lives on the same machine and user,
  with no passphrase.

## Testing

`tests/features/test_vault*.py` gives the feature a store whose `age` is a script in a temporary
folder, so no test needs the real one. The tests read every route's body for five forms of a bait
value with `vault.forms` and `vault.scan`.
