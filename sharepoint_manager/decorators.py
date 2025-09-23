import time
import functools
from random import randint
from typing import Callable, Any, TypeVar, cast
import warnings


class RepetitionException(Exception):
    """Error created to set specific Exceptions to be repeated
    by the `retry` decorator."""


F = TypeVar("F", bound=Callable[..., Any])


def exponential_time(
    initial: float, exp_base: float, max_delay: float, jitter_ms: float, attempt: int
) -> float:
    next_delay: float = initial * exp_base ** (attempt - 1) + randint(0, int(abs(jitter_ms))) / 1000
    return min(next_delay, max_delay)


def retry(
    attempts: int,
    exceptions: type[BaseException] | tuple[type[BaseException], ...],
    delay_base: int = 2,
    max_delay: int = 60,
    jitter_ms: int = 0,
) -> Callable[[F], F]:
    """Retry decorator."""

    if not (isinstance(exceptions, type) or isinstance(exceptions, tuple)):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError("exceptions must be an Error/Exception or a tuple of Error/Exception")  # pyright: ignore[reportUnreachable]

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt = 1
            while attempt < attempts:
                try:
                    return func(*args, **kwargs)
                except exceptions:
                    time.sleep(
                        exponential_time(
                            initial=1,
                            exp_base=delay_base,
                            max_delay=max_delay,
                            jitter_ms=jitter_ms,
                            attempt=attempt,
                        )
                    )
                attempt += 1
            return func(*args, **kwargs)

        return cast(F, wrapper)

    return decorator


def retry_if_not_exception(
    attempts: int,
    exceptions: type[BaseException] | tuple[type[BaseException], ...],
    delay_base: int = 2,
    max_delay: int = 60,
    jitter_ms: int = 0,
) -> Callable[[F], F]:
    """Retry if not (mapped) exception decorator."""

    if not (isinstance(exceptions, type) or isinstance(exceptions, tuple)):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError("exceptions must be an Error/Exception or a tuple of Error/Exception")  # pyright: ignore[reportUnreachable]

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt = 1
            while attempt < attempts:
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    raise exc
                except Exception:
                    time.sleep(
                        exponential_time(
                            initial=1,
                            exp_base=delay_base,
                            max_delay=max_delay,
                            jitter_ms=jitter_ms,
                            attempt=attempt,
                        )
                    )
                attempt += 1
            return func(*args, **kwargs)

        return cast(F, wrapper)

    return decorator


def deprecated(
    deprecated_on_version: str | None = None,
    removed_on_version: str | None = None,
    current_version: str | None = None,
    details: str | None = None,
) -> Callable[[F], F]:
    """Deprecation decorator that prints a warning if the function is deprecated."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            warnings.warn(
                (
                    f"Call to deprecated function: {func.__name__}\n"
                    f"Function was deprecated on version: {deprecated_on_version}\n"
                    f"Function will be removed on version: {removed_on_version}\n"
                    f"Current version: {current_version}\n"
                    f"Details: {details}"
                ),
                category=DeprecationWarning,
                stacklevel=2,
            )
            return func(*args, **kwargs)

        return cast(F, wrapper)

    return decorator
