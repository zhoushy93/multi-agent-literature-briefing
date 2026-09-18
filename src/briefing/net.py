"""Small HTTP helpers shared by the sources and the model client.

Lives outside both so that neither has to import the other.
"""

from __future__ import annotations


def parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header in delta-seconds form.

    The HTTP-date form is not supported; callers fall back to exponential
    backoff, which is what most APIs expect anyway.
    """
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return max(seconds, 0.0)
