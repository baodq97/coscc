"""The one app the page and the handlers share: the FastAPI app and its `Service`."""

from coscc.api import build

API = build()
SERVICE = API.state.service
