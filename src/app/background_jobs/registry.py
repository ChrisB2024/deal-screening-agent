from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Coroutine

from pydantic import BaseModel

from .types import JobContext, UnknownJobType

HandlerFn = Callable[[Any, JobContext], Coroutine[Any, Any, None]]


@dataclass(frozen=True)
class HandlerRegistration:
    handler: HandlerFn
    schema: type[BaseModel]
    max_attempts: int


_registry: dict[str, HandlerRegistration] = {}


def register(
    job_type: str,
    *,
    schema: type[BaseModel],
    max_attempts: int = 5,
):
    def decorator(fn: HandlerFn) -> HandlerFn:
        _registry[job_type] = HandlerRegistration(
            handler=fn,
            schema=schema,
            max_attempts=max_attempts,
        )
        return fn

    return decorator


def get_handler(job_type: str) -> HandlerRegistration:
    reg = _registry.get(job_type)
    if reg is None:
        raise UnknownJobType(f"No handler registered for job type: {job_type}")
    return reg


def is_registered(job_type: str) -> bool:
    return job_type in _registry


def registered_types() -> list[str]:
    return list(_registry.keys())


def get_max_attempts(job_type: str) -> int:
    return get_handler(job_type).max_attempts
