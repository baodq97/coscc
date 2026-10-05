"""`uv run python -m coscc.http > ui/src/api.gen.ts`: the studio's types, made from the routes."""

from coscc.http.app import typescript

print(typescript(), end="")
