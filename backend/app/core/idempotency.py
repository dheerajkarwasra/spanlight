"""Idempotency keys: a retried POST runs once and is answered with the first answer.

A route opts in by wrapping its authorization dependency: `Depends(idempotent(require(...)))`. A
client that sends an `Idempotency-Key` header (at most 128 visible ASCII characters) gets this:

* The first request runs. Its status, JSON body and content type are stored for 24 hours,
  whatever the status, except a 5xx: then the key is released so the retry can run.
* The same key with the same request (method, path and canonical body) after that is answered
  with the stored response and `Idempotent-Replayed: true`, and the route does not run again.
* The same key with a different request is 422 `IDEMPOTENCY_MISMATCH`.
* The same key while the first request is still running is 409 `IDEMPOTENCY_IN_PROGRESS`, unless
  that request has been silent for over a minute: it is then taken to have died and is replaced.

A key belongs to one principal (`principal_key`), so two people can use the same key. Without the
header the dependency does nothing but authorize.

Using it on a route::

    @router.post("/projects/{project_id}/things", status_code=202)
    async def create_thing(
        access: Annotated[Access, Depends(idempotent(require(Permission.X)))],
        body: ThingIn,
    ) -> ThingOut: ...

Today four routes opt in: creating an alert channel, creating an export, starting an experiment
run and running the playground. `tests/core/test_idempotency_replays.py` replays each of them.

`idempotent(authorize)` runs `authorize` as its own dependency and hands its result to the route,
so a request is always authorized before a key is reserved, and a replay is authorized exactly like
the request it replays: a caller who lost access since gets the 403 or 404, not the stored answer.
Do not declare `authorize` on the route as well; FastAPI would run it twice.

How it works, and why in two parts. Reserving the key has to happen after the caller has been
authenticated and before the handler runs, so it is a dependency, which can also short-circuit a
replay by raising `IdempotentReplay`. Storing the answer needs the response as the client receives
it, including errors that exception handlers turn into responses, and no dependency or route class
sees that. `IdempotencyMiddleware` sits just outside the exception handlers: once the dependency
has reserved a key it holds the response back until its last chunk, stores it (or releases the key
on a 5xx, a crash or an answer it cannot keep), and only then lets it go, so a client that retries
the moment it has an answer always finds one. A request that did not reserve a key, which is every
request without the header, passes through untouched.

The answer must be JSON and under 1 MiB, as it is kept in a `jsonb` column. The middleware decides
when the response starts or as soon as it grows too large: anything else (a 5xx, another content
type, a file) is released and passed on without being held. Only the status, body and content type
are replayed, not other headers. The answer is kept as plain text in the database for a day, so a
route whose answer carries a secret that is shown once (a new API key, a new token) must not use
this.

The store uses its own small connection pool (`app.state.idempotency_session_factory`): it takes a
connection while the request still holds one from the main pool.
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Annotated, cast

import structlog
from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.deps import CurrentPrincipal
from app.api.principals import ApiKeyPrincipal, Principal
from app.core.idempotency_store import (
    Reservation,
    SessionFactory,
    StoredResponse,
    complete,
    release,
    reserve,
)

logger = structlog.get_logger(__name__)

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"
MAX_KEY_LENGTH = 128
MAX_KEPT_BODY_BYTES = 1024 * 1024
_KEY_PATTERN = r"^[\x21-\x7e]+$"  # visible ASCII: no spaces, controls or non-ASCII

_SCOPE_KEY = "spanlight.idempotency"
_KEY_HEADER_BYTES = IDEMPOTENCY_KEY_HEADER.lower().encode()


def principal_key(principal: Principal) -> str:
    """Who owns an idempotency key: `user:<id>` for a person, `key:<id>` for an API key.

    A session and a personal access token of one user share keys on purpose: a script and the
    dashboard retrying the same action should not both run it.
    """
    if isinstance(principal, ApiKeyPrincipal):
        return f"key:{principal.key_id}"
    return f"user:{principal.user.id}"


def request_fingerprint(method: str, path: str, body: bytes) -> bytes:
    """A SHA-256 over the method, the path and the body, which ignores how the JSON is written.

    A body that parses as JSON is hashed in canonical form (sorted keys, no whitespace), so key
    order and spacing do not make a retry a different request. Any other body is hashed as is.
    """
    digest = hashlib.sha256()
    for part in (method.upper().encode(), path.encode(), _canonical(body)):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.digest()


def _canonical(body: bytes) -> bytes:
    if not body:
        return b""
    try:
        parsed = json.loads(body)
        return json.dumps(
            parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    except (ValueError, RecursionError):
        return body


class IdempotentReplay(Exception):  # noqa: N818 - it is a response, not an error
    """Raised by the dependency to answer with a stored response instead of running the route."""

    def __init__(self, response: StoredResponse) -> None:
        super().__init__("replaying a stored response")
        self.response = response


class _Slot:
    """Passed from the middleware to the dependency through the ASGI scope.

    The dependency fills in the reservation; the middleware reads it after the response exists.
    """

    def __init__(self) -> None:
        self.reservation: Reservation | None = None


def idempotent[T](authorize: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """Make a POST route accept an `Idempotency-Key`, after `authorize` has let the caller in.

    `authorize` is the route's authorization dependency, such as `require(Permission.X)`. The
    result is a dependency that returns whatever `authorize` returned.
    """

    async def dependency(
        request: Request,
        access: Annotated[T, Depends(authorize)],
        principal: CurrentPrincipal,
        idempotency_key: Annotated[
            str | None,
            Header(
                alias=IDEMPOTENCY_KEY_HEADER,
                min_length=1,
                max_length=MAX_KEY_LENGTH,
                pattern=_KEY_PATTERN,
                description="Makes a retry of this request safe: it runs once, then is replayed.",
            ),
        ] = None,
    ) -> T:
        if idempotency_key is not None:
            await _reserve(request, principal, idempotency_key)
        return access

    return dependency


async def _reserve(request: Request, principal: Principal, key: str) -> None:
    slot = request.scope.get(_SCOPE_KEY)
    if not isinstance(slot, _Slot):
        raise RuntimeError(
            "idempotent() needs IdempotencyMiddleware: call install_idempotency(app) when "
            "building the app"
        )
    request_hash = request_fingerprint(request.method, request.url.path, await request.body())
    session_factory: SessionFactory = request.app.state.idempotency_session_factory
    outcome = await reserve(session_factory, principal_key(principal), key, request_hash)
    if isinstance(outcome, StoredResponse):
        raise IdempotentReplay(outcome)
    slot.reservation = outcome


class IdempotencyMiddleware:
    """Stores the response of a request that reserved a key, or releases the key."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _carries_key(scope):
            await self.app(scope, receive, send)
            return

        exchange = _Exchange(scope["app"].state.idempotency_session_factory, send)
        scope[_SCOPE_KEY] = exchange.slot
        try:
            await self.app(scope, receive, exchange.send)
        except BaseException:
            # A response that had begun is dropped with the exception, and Starlette's outermost
            # handler then answers 500 as if nothing had been sent.
            await exchange.abandon()
            raise
        await exchange.finish()


def _carries_key(scope: Scope) -> bool:
    return any(name == _KEY_HEADER_BYTES for name, _ in scope.get("headers", []))


class _Exchange:
    """One request that carries a key: its slot, and its response while that is held back.

    Until the dependency has reserved the key the response is not ours to keep (it is an
    authentication error, a validation error, or the answer to a replay) and passes through.
    After that the response is held only while it can still be kept: it is decided when it starts
    (a 5xx or a content type other than JSON is released and passed on), when it outgrows
    `MAX_KEPT_BODY_BYTES`, or when a message arrives that is not a plain body (a file sent by
    path). Otherwise it is held until its last chunk, stored, and only then delivered. That moment,
    not the end of the app, is used because Starlette runs a response's background tasks after the
    last chunk, and they must not hold the client up.
    """

    def __init__(self, session_factory: SessionFactory, send: Send) -> None:
        self.slot = _Slot()
        self._session_factory = session_factory
        self._send = send
        self._held: list[Message] = []
        self._held_bytes = 0
        self._decided = False  # nothing more will be held: stored, released or passed through

    async def send(self, message: Message) -> None:
        reservation = self.slot.reservation
        if reservation is None or self._decided:
            await self._send(message)
            return
        self._held.append(message)
        kind = message["type"]
        if kind == "http.response.start":
            reason = _not_keepable(message)
            if reason is not None:
                await self._pass_through(reservation, reason)
        elif kind == "http.response.body":
            self._held_bytes += len(message.get("body", b""))
            if self._held_bytes > MAX_KEPT_BODY_BYTES:
                await self._pass_through(reservation, "too_large")
            elif not message.get("more_body", False):
                self._decided = True
                await self._settle(reservation)
                await self._flush()
        else:
            await self._pass_through(reservation, "unstorable")

    async def finish(self) -> None:
        """The app has returned: free a key whose response never completed, and let it go."""
        reservation = self.slot.reservation
        if reservation is not None and not self._decided:
            await self._pass_through(reservation, "no_response")

    async def abandon(self) -> None:
        """The app raised: free the key and drop what was held."""
        reservation = self.slot.reservation
        if reservation is not None and not self._decided:
            self._decided = True
            self._held.clear()
            await _give_up(self._session_factory, reservation, "unhandled_exception")

    async def _pass_through(self, reservation: Reservation, reason: str) -> None:
        """Give up the key and deliver what is held, and everything after it, as it comes."""
        self._decided = True
        await _give_up(self._session_factory, reservation, reason)
        await self._flush()

    async def _flush(self) -> None:
        held, self._held = self._held, []
        for message in held:
            await self._send(message)

    async def _settle(self, reservation: Reservation) -> None:
        """Store the held response under the reservation, or release the key if not kept."""
        start = next((m for m in self._held if m["type"] == "http.response.start"), None)
        body = b"".join(m.get("body", b"") for m in self._held if m["type"] == "http.response.body")
        stored = None if start is None else _storable(start, body)
        if stored is None:
            await _give_up(self._session_factory, reservation, "not_keepable")
            return
        try:
            owned = await complete(self._session_factory, reservation, stored)
        except Exception:
            # The request has run and has an answer: failing it now would be worse than not
            # remembering it. The key stays in flight and is taken over after a minute.
            logger.exception("idempotency_store_failed", principal_id=reservation.principal_id)
            return
        if not owned:
            logger.warning("idempotency_lease_lost", principal_id=reservation.principal_id)


def _is_json(content_type: str) -> bool:
    media_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


def _not_keepable(start: Message) -> str | None:
    """Why a response, from its first message alone, can never be kept; None if it might be."""
    if int(start["status"]) >= 500:
        return "server_error"
    content_type = _header(start, b"content-type")
    if content_type is not None and not _is_json(content_type):
        return "not_json"
    return None


def _storable(start: Message, body: bytes) -> StoredResponse | None:
    """The response in the form the table keeps, or None when it cannot be kept faithfully."""
    content_type = _header(start, b"content-type")
    if not body:
        return StoredResponse(int(start["status"]), None, content_type)
    if content_type is None or not _is_json(content_type):
        return None
    try:
        parsed = json.loads(body)
    except (ValueError, RecursionError):
        return None
    if parsed is None:
        # A JSON `null` and "no body" are both None once parsed, and the table keeps no body as
        # SQL NULL, so a replay could not tell them apart. No route answers with a bare `null`.
        return None
    return StoredResponse(int(start["status"]), parsed, content_type)


def _header(start: Message, name: bytes) -> str | None:
    for key, value in start.get("headers", []):
        if key.lower() == name:
            return bytes(value).decode("latin-1")
    return None


async def _give_up(session_factory: SessionFactory, reservation: Reservation, reason: str) -> None:
    """Release the key so a retry can run. Never raises: a stuck key frees itself in a minute."""
    try:
        await release(session_factory, reservation)
    except Exception:
        logger.exception("idempotency_release_failed", principal_id=reservation.principal_id)
        return
    logger.info("idempotency_released", principal_id=reservation.principal_id, reason=reason)


async def _replay(_: Request, exc: Exception) -> Response:
    stored = cast(IdempotentReplay, exc).response
    headers = {REPLAYED_HEADER: "true"}
    if stored.body is None:
        return Response(status_code=stored.status, headers=headers, media_type=stored.content_type)
    return JSONResponse(
        stored.body, status_code=stored.status, headers=headers, media_type=stored.content_type
    )


def install_idempotency(app: FastAPI) -> None:
    """Add what `idempotent()` needs: the replay handler and the middleware.

    Call it before the other middleware is added, so this one sits closest to the exception
    handlers and sees the response a route's error became.
    """
    app.add_exception_handler(IdempotentReplay, _replay)
    app.add_middleware(IdempotencyMiddleware)
