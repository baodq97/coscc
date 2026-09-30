---
paths:
  - "coscc/auth.py"
  - "coscc/run.py"
---

# Things that break here

- The guard is the only door: a route answers without a session only if the exempt list names
  it. Moving the guard into `api_transformer` lets Reflex answer `OPTIONS` without it.
- `proxy_headers=False`: behind a proxy every client shares one failure count, so a stranger can
  lock the owner out.
- A state-changing request or socket handshake whose `Origin` differs from `Host` is `403`; a
  proxy that rewrites `Host` breaks the page.
- Password hashing is memory-heavy, so it is bounded; over the bound answers `429`.
- A page the guard lets through goes out `Cache-Control: no-cache`, or a browser reuses a cached
  page after logout.
