"""
Environment variable helper for zero-hardcoding configuration.

Every setting can be overridden by an environment variable.
Pattern: FIN_{SECTION}_{KEY} (e.g. FIN_APP_HOST, FIN_API_PORT)
"""

from typing import Any


def env_get(key: str, default: Any = None, env_prefix: str = "FIN_") -> Any:
    """Get a value from environment variables with a default fallback.

    Args:
        key: Setting key (e.g. "APP_HOST" -> checks "FIN_APP_HOST").
        default: Fallback value if not in env.
        env_prefix: Environment variable prefix (default "FIN_").

    Returns:
        The environment variable value (as string) or the default.
    """
    import os

    env_key = f"{env_prefix}{key}"
    env_value = os.environ.get(env_key)
    if env_value is not None:
        return env_value
    return default


def env_int(key: str, default: int, env_prefix: str = "FIN_") -> int:
    """Get an integer from environment variables with a default fallback."""
    val = env_get(key, default, env_prefix)
    if isinstance(val, int):
        return val
    return int(val)


def env_float(key: str, default: float, env_prefix: str = "FIN_") -> float:
    """Get a float from environment variables with a default fallback."""
    val = env_get(key, default, env_prefix)
    if isinstance(val, float):
        return val
    return float(val)


def env_bool(key: str, default: bool, env_prefix: str = "FIN_") -> bool:
    """Get a boolean from environment variables with a default fallback.

    Accepts: 1/0, true/false, yes/no, on/off (case-insensitive).
    """
    val = env_get(key, default, env_prefix)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("1", "true", "yes", "on")
    return bool(val)


def env_list(key: str, default: list, env_prefix: str = "FIN_", delimiter: str = ",") -> list:
    """Get a list from environment variables with a default fallback.

    Values are comma-separated strings (configurable delimiter).
    """
    val = env_get(key, default, env_prefix)
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        return [item.strip() for item in val.split(delimiter) if item.strip()]
    return val
