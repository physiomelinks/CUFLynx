"""``working_dir`` on the wire, and the names it replaces.

The client sends ``working_dir`` for the study's folder. The routes still declare
the names they had (``output_dir``, ``outputs_dir``, ``config_outputs_dir``), so this
ASGI middleware copies ``working_dir`` onto those before a request reaches them: in
the query string, and in a JSON body at any depth (a run's ``settings`` carries it
too). A name the request already sets is left alone. Clients that still send the old
names keep working unchanged; the old names are accepted for one more release.

``/api/outputs/<route>`` is likewise rewritten to ``/api/working-dir/<route>``, the
route's name now.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qsl, urlencode

WIRE_NAME = "working_dir"
LEGACY_NAMES = ("output_dir", "outputs_dir", "config_outputs_dir")

ROUTE_PREFIX = "/api/working-dir/"
LEGACY_ROUTE_PREFIX = "/api/outputs/"


def with_legacy_names(value):
    """``value`` with ``working_dir`` copied onto each legacy name it lacks, recursively."""
    if isinstance(value, list):
        return [with_legacy_names(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {k: with_legacy_names(v) for k, v in value.items()}
    if WIRE_NAME in out:
        for name in LEGACY_NAMES:
            out.setdefault(name, out[WIRE_NAME])
    return out


def query_with_legacy_names(query: bytes) -> bytes:
    pairs = parse_qsl(query.decode("latin-1"), keep_blank_values=True)
    names = {k for k, _ in pairs}
    value = next((v for k, v in pairs if k == WIRE_NAME), None)
    if value is None:
        return query
    pairs += [(name, value) for name in LEGACY_NAMES if name not in names]
    return urlencode(pairs).encode("latin-1")


class WorkingDirAliases:
    """ASGI middleware: see the module docstring."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        scope = dict(scope)
        path = scope.get("path", "")
        if path.startswith(LEGACY_ROUTE_PREFIX):
            scope["path"] = ROUTE_PREFIX + path[len(LEGACY_ROUTE_PREFIX):]
            scope["raw_path"] = scope["path"].encode("utf-8")
        query = scope.get("query_string") or b""
        if WIRE_NAME.encode() in query:
            scope["query_string"] = query_with_legacy_names(query)

        headers = dict(scope.get("headers") or [])
        content_type = headers.get(b"content-type", b"").split(b";")[0].strip()
        if content_type != b"application/json":
            await self.app(scope, receive, send)
            return

        chunks = []
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                # A disconnect before the body arrived: hand it on as it came.
                async def replay(message=message):
                    return message
                await self.app(scope, replay, send)
                return
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)
        if WIRE_NAME.encode() in body:
            try:
                body = json.dumps(with_legacy_names(json.loads(body))).encode("utf-8")
            except ValueError:
                pass  # not JSON after all: the route reports it
        scope["headers"] = [(k, v) for k, v in scope.get("headers") or []
                            if k != b"content-length"] + [
            (b"content-length", str(len(body)).encode())]

        sent = False

        async def receive_body():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, receive_body, send)
