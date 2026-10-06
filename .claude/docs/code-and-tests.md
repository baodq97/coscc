# Code and tests

## The checks

`npm test` runs lint, then every test; CI runs it on every pull request. Before a commit:

```
uv run ruff format && uv run ruff check --fix
uv run pytest tests/<pkg>/test_<module>.py      # the modules you changed
```

`tests/conftest.py` gives every test its own data root and answers the loop in the process;
a test of the loop's child itself is marked `real_loop`. A test that hangs fails at 60 s.

Fix the code, not the check. A rule is switched off only in `pyproject.toml`, with its reason; a
`# noqa` or `# ty: ignore` names its rule and why on the same line. No `cast`. Refuse first:
return, raise or continue on the failing case, so the main path is unindented.

Repository-wide tests name the fix in their failure: comments, citations, layers (a module
imports its own package or lower, without a cycle; a new package gets a `LAYERS` line) and
boundaries. Boundary lists (private imports, table owners, `dict[str, Any]`) only shrink: fix
the code, not the list.

## Types, errors, imports

- A closed set of strings is a `Literal` whose tuple comes from `get_args`; a value read from
  JSON, the database or a request is checked against it once, where it is read.
- Catch an error by its type (`data.Unusable` for the database). `except Exception` is for what
  nobody expected: it calls `log.exception`. Only cleanup on the way out, or a poll that fails
  the same way offline, swallows silently, with a `noqa` and why.
- Import a name from the module that defines it: no re-export, alias or shim. Moving a name moves
  its importers and `mock.patch` strings in the same commit; a test patches where the name is
  looked up.

## Adding a feature

New work is a folder `coscc/features/<name>/`: `__init__.py` ending in one `FEATURE`,
`README.md` (what it does, its routes, tables and events), a line in `FEATURES`, and its tests in
`tests/features/<name>/`. It imports only `coscc/kernel.py`, the one module that hands on names it does not define;
`KERNEL_GAPS` in `tests/test_layers.py` lists what the kernel does not give yet and only shrinks.
Copy `notices`. A need no extension point serves is a kernel change, planned first.

- Extension points: `routes`, `tables`, `agent` giving `Parts` of `Tool`, `Guard`
  (`check(Facts)` returns words to deny, or `None`; asked before every step and integration) and `Block` (`render(Facts)` adds prompt
  text); `sessions=(Session(kind,
  grant, output, purpose),)`, a paid session it runs through `ctx.agents.session(cwd, kind, prompt)`;
  `output` is its declaration `{kind: session, version, fields}` (`coscc/units/contracts.py`).
  The core never writes a feature's name (`CoreNamesNoFeature` in `tests/test_boundaries.py`).
- `Ctx` is built for this feature alone: `units` (`key`, `create_unit`, `main_tree`, `units`,
  `open_prs`, `own_tree`), `runs` (`journal`, `interventions`), `agents` (`session`), `store` (the
  database; only your own tables), `bus`, `settings` (`state`, `enabled`, `arm`, `schedule`,
  `set_schedule`), `refuse_updating`, `asks` (slow reads held and asked again), `required_checks`.
  Writing git is the release feature's alone (`OnlyReleaseWritesGit`). A test builds
  the `Ctx` it needs with `tests/features/ctx.py` `ctx_for`; a handle it names not raises when touched.
- Building blocks: `kernel.body/line/ndjson`.
- UI: an optional `coscc/features/<name>/ui/index.tsx` exporting `ui: FeatureUI`
  (`ui/src/lib/feature.tsx`): `topbar`, `unit` and `backlog` components the studio draws in its
  slots while the feature is not off for the workspace, and `page`, a sidebar entry and the
  screen at `/feature/<name>`. The studio finds it with `import.meta.glob` at build time. Import
  its parts as `@studio/...` (`lib/api` `api`, `useResource`, `useFollow`; `components/ui`;
  `lib/format`), never copy them; type the routes the screen reads in Python (a `TypedDict`) and
  run `uv run python -m coscc.http > ui/src/api.gen.ts`. `npm --prefix ui run check` types it.

```python
"""Bookmarks: a note per unit."""

from coscc.kernel import Ctx, Facts, Feature, Guard, Parts, body
from fastapi import APIRouter, Request

TABLE = "CREATE TABLE IF NOT EXISTS bookmarks (unit TEXT PRIMARY KEY, note TEXT NOT NULL)"


def routes(ctx: Ctx):
    router = APIRouter()

    @router.post("/api/bookmarks")
    async def save(request: Request) -> dict[str, str]:
        sent = await body(request)
        with ctx.store.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO bookmarks VALUES (?, ?)", (sent["unit"], sent["note"])
            )
        return {"unit": sent["unit"]}

    return router.routes


def no_ship(facts: Facts) -> str | None:
    return "no ship yet" if facts.stage == "ship" else None


FEATURE = Feature(
    "bookmarks", routes, tables=(TABLE,), agent=lambda _: Parts(guards=(Guard("b", no_ship),))
)
```

```python
class TheGuardOnlyDenies(unittest.TestCase):
    def test_it_denies_ship_and_abstains_elsewhere(self):
        self.assertTrue(bookmarks.no_ship(mock.Mock(spec=Facts, stage="ship")))
        self.assertIsNone(bookmarks.no_ship(mock.Mock(spec=Facts, stage="impl")))
```

Rules: a feature imports only `coscc.kernel` and its own folder, never another feature, and owns the tables it creates; at most 3 files; `## What the agent sees` in its doc when it
has agent parts; a blocking tool handler awaits `asyncio.to_thread`; a handler that runs a
command reads its line with `kernel.bash_refused` and its own `Places`; a guard only denies or
abstains.

## Tests

- `tests/` mirrors `coscc/`: `coscc/<pkg>/<module>.py` is tested by
  `tests/<pkg>/test_<module>.py`; a file too big splits as `test_<module>_<aspect>.py`. Every
  directory has an `__init__.py`.
- A test class subclasses `unittest.TestCase` and is named as a sentence with no `Test` prefix:
  pytest skips a plain class silently. A method's name says the behaviour, never the unit.
- A thin wrapper around a command gets no test; its callers take a fake and their tests check
  the decisions.
- Fixtures go in `testdata/` beside the test. A test waits on the effect it asserts, never a
  fixed time.
