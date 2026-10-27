from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

import asyncpg
from sqlalchemy.exc import IntegrityError

from app.domain.common import NameConflictError


_P = ParamSpec("_P")
_R = TypeVar("_R")


def translate_unique_violation(
    operation: Callable[_P, Awaitable[_R]],
) -> Callable[_P, Awaitable[_R]]:
    """Translate unique violations from SQLAlchemy or asyncpg to a domain conflict."""

    @wraps(operation)
    async def translated(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            return await operation(*args, **kwargs)
        except IntegrityError as error:
            if getattr(error.orig, "sqlstate", None) != "23505":
                raise
            raise NameConflictError("name already exists") from error
        except asyncpg.UniqueViolationError as error:
            raise NameConflictError("name already exists") from error

    return translated
