from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar, cast, overload

from discord.ext import commands

from dankmemer import ClientState, ConfigurationError, DankMemer
from dankmemer._event_client import (
    _ARGUMENT_COUNTS,  # pyright: ignore[reportPrivateUsage]
    DEPENDENCIES,
)
from dankmemer._storage import checked_subscription_id

__all__ = ("bind", "event", "listen", "unbind")

_Listener = Callable[..., Awaitable[None]]
_Callback = TypeVar("_Callback", bound=_Listener)
_METADATA = "__dankmemer_cog_listeners__"
_BINDING = "__dankmemer_cog_binding__"


@dataclass(frozen=True, slots=True)
class _Spec:
    event: str
    subscription_id: str | None
    primary: bool


@dataclass(frozen=True, slots=True)
class _Binding:
    dank: DankMemer
    listener_ids: tuple[int, ...]


def _specs(callback: object) -> tuple[_Spec, ...]:
    value: object = getattr(callback, _METADATA, ())
    if not isinstance(value, tuple):
        raise ConfigurationError("invalid Cog listener metadata")
    entries = cast(tuple[object, ...], value)
    if not all(isinstance(entry, _Spec) for entry in entries):
        raise ConfigurationError("invalid Cog listener metadata")
    return cast(tuple[_Spec, ...], entries)


def _mark(
    callback: _Callback,
    name: str | None,
    subscription_id: str | None,
    *,
    primary: bool,
) -> _Callback:
    _check_async_method(callback)
    event_name = _event_name(
        getattr(callback, "__name__", None) if name is None else name
    )
    if subscription_id is not None:
        checked_subscription_id(subscription_id)
    previous = _specs(callback)
    if any(spec.event == event_name for spec in previous):
        raise ConfigurationError("Cog listener is marked twice for the same event")
    setattr(
        callback, _METADATA, (*previous, _Spec(event_name, subscription_id, primary))
    )
    return callback


def _check_async_method(value: object) -> None:
    if not inspect.iscoroutinefunction(value):
        raise ConfigurationError("Cog listeners must be async methods")


def _event_name(value: object) -> str:
    if not isinstance(value, str) or value.removeprefix("on_") not in DEPENDENCIES:
        raise ConfigurationError("unknown event name")
    return value.removeprefix("on_")


def _checked_cog(value: object) -> commands.Cog:
    if not isinstance(value, commands.Cog):
        raise ConfigurationError("expected a commands.Cog")
    return value


def _checked_client(value: object) -> DankMemer:
    if not isinstance(value, DankMemer):
        raise ConfigurationError("expected a DankMemer client")
    return value


@overload
def event(
    callback: _Callback, /, *, subscription_id: str | None = None
) -> _Callback: ...


@overload
def event(
    *, subscription_id: str | None = None
) -> Callable[[_Callback], _Callback]: ...


def event(
    callback: _Callback | None = None, /, *, subscription_id: str | None = None
) -> _Callback | Callable[[_Callback], _Callback]:
    """Mark a Cog's primary async handler named ``on_<event>``.

    Use ``@dank_cog.event`` or ``@dank_cog.event(subscription_id="...")``
    above an instance method. :func:`bind` registers it with the Cog's
    client. Binding replaces that client's previous primary handler for
    the event, while retaining additional listeners. Unloading the Cog
    does not restore a replaced handler.

    The default subscription ID is ``on_<event>``. An explicit ID identifies
    the consumer across method renames and application restarts.
    The method and its type are returned unchanged; decoration makes no requests.
    """
    if callback is None:

        def decorator(listener: _Callback) -> _Callback:
            return _mark(listener, None, subscription_id, primary=True)

        return decorator
    return _mark(callback, None, subscription_id, primary=True)


def listen(
    name: str | None = None, *, subscription_id: str | None = None
) -> Callable[[_Callback], _Callback]:
    """Mark an additional Cog listener without replacing other handlers.

    ``name`` accepts an event with or without ``on_`` and defaults to the
    method's name. Different Cogs can listen to the same event. Durable
    delivery requires an explicit, stable ``subscription_id`` for each
    consumer. Register the marked methods with :func:`bind` in ``cog_load``.
    The method and its type are returned unchanged.
    """

    def decorator(callback: _Callback) -> _Callback:
        return _mark(callback, name, subscription_id, primary=False)

    return decorator


def bind(cog: commands.Cog, dank: DankMemer) -> None:
    """Register a Cog's decorated methods with its Dank Memer client.

    Call this at the end of ``cog_load``, after other startup work succeeds.
    Inherited listeners follow Python's method resolution order; an override
    without a decorator removes the inherited registration. All listeners
    are validated before any are registered or primary handlers replaced.
    Signatures must accept the event's positional arguments after ``self``.

    Binding the same Cog to the same client again has no effect. To move it
    to another client, :func:`unbind` it first. Registration starts polling
    only if the supplied client is already running. This function does not
    start the client, create a session, or take ownership of it.

    Raises :class:`~dankmemer.ConfigurationError` for invalid methods,
    subscription conflicts, or a Cog bound to another client, and
    :class:`~dankmemer.LifecycleError` if the client is closing or closed.
    """
    cog = _checked_cog(cog)
    dank = _checked_client(dank)
    dank._check_listener_access()  # pyright: ignore[reportPrivateUsage]
    current: object = getattr(cog, _BINDING, None)
    if isinstance(current, _Binding):
        if current.dank is not dank:
            raise ConfigurationError("Cog is already bound to another client")
        return

    listeners: list[tuple[_Listener, str, str | None, bool]] = []
    seen: set[str] = set()
    # Read class dictionaries so unrelated properties never run during discovery.
    for cls in type(cog).__mro__:
        for name, member in vars(cls).items():
            if name in seen:
                continue
            seen.add(name)
            if isinstance(member, (staticmethod, classmethod)):
                if _specs(getattr(cast(object, member), "__func__", None)):
                    raise ConfigurationError("Cog listeners must be instance methods")
                continue
            specs = _specs(member)
            if not specs:
                continue
            bound: object = getattr(cog, name)
            if not inspect.ismethod(bound) or bound.__self__ is not cog:
                raise ConfigurationError("Cog listeners must be bound instance methods")
            callback = cast(_Listener, bound)
            for spec in specs:
                try:
                    inspect.signature(callback).bind(
                        *[None] * _ARGUMENT_COUNTS[spec.event]
                    )
                except TypeError as error:
                    raise ConfigurationError(
                        f"Cog listener {name} cannot accept {spec.event} arguments"
                    ) from error
                listeners.append(
                    (callback, spec.event, spec.subscription_id, spec.primary)
                )

    listener_ids = dank._bind_listeners(listeners)  # pyright: ignore[reportPrivateUsage]
    setattr(cog, _BINDING, _Binding(dank, listener_ids))


def unbind(cog: commands.Cog) -> None:
    """Remove only the registrations created by :func:`bind` for this Cog.

    Call this from ``cog_unload``. Repeated calls and cleanup after SDK
    shutdown are safe. Primary handlers replaced by another Cog are left
    alone. Best-effort queued calls are discarded; durable calls remain
    saved for a handler registering the same subscription ID later.
    Running callbacks follow the client's normal listener removal behaviour.
    The shared client and its other consumers remain open.
    """
    cog = _checked_cog(cog)
    current: object = getattr(cog, _BINDING, None)
    if not isinstance(current, _Binding):
        return
    if current.dank.state not in (ClientState.CLOSING, ClientState.CLOSED):
        for listener_id in current.listener_ids:
            current.dank.remove_listener(listener_id)
    delattr(cog, _BINDING)
