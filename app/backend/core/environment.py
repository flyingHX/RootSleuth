import logging
import os

logger = logging.getLogger(__name__)

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def get_env_bool(name: str, *, default: bool) -> bool:
    """Read an explicit boolean environment variable.

    Environment variables are strings, so checking only whether a variable is
    present incorrectly treats ``"False"`` as true. Empty or missing values use
    the supplied default; invalid non-empty values fail fast during startup.
    """
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default

    normalized = raw_value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(f"Invalid boolean value for {name}: {raw_value!r}")


def get_env_int(name: str, *, default: int) -> int:
    """Read an integer environment variable, falling back on empty/invalid values."""
    raw_value = os.environ.get(name, "").strip()
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        logger.warning("Invalid integer for %s: %r; using default %d", name, raw_value, default)
        return default
