---
paths:
  - "coscc/auth.py"
  - "coscc/run.py"
---

# Things that break here

- The guard is the only door: a route answers without a session only if the exempt list names
  it. It wraps the whole app (`run.served`); inside the app a middleware would miss scopes.
- `proxy_headers=False`: behind a proxy every client shares one failure count, so a stranger can
  lock the owner out.
- A state-changing request whose `Origin` differs from `Host` is `403`; a proxy that rewrites
  `Host` breaks the page. A websocket is always closed: the app has none.
- Password hashing is memory-heavy, so it is bounded; over the bound answers `429`.
- A page the guard lets through goes out `Cache-Control: no-cache`, or a browser reuses a cached
  page after logout.
