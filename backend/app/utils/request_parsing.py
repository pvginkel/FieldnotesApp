"""Helpers for parsing common request parameter types."""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

_TRUE_VALUES = {"true", "1", "yes", "on"}


def parse_bool_query_param(raw_value: str | None, *, default: bool = False) -> bool:
    """Interpret a truthy query parameter value with a configurable default."""
    if raw_value is None:
        return default
    return raw_value.lower() in _TRUE_VALUES


def parse_enum_list_query_param[EnumType: Enum](
    raw_values: Sequence[str] | None,
    enum_cls: type[EnumType],
) -> list[EnumType]:
    """Parse repeated or comma-separated enum query parameters.

    Args:
        raw_values: Raw query parameter values as provided by Flask/Werkzeug.
        enum_cls: Enumeration class to parse against.

    Returns:
        A list of unique enum members preserving the original ordering.

    Raises:
        ValueError: When one or more values cannot be parsed into the enum.
    """
    if not raw_values:
        return []

    tokens: list[str] = []
    for raw_value in raw_values:
        if not raw_value:
            continue
        tokens.extend(segment.strip() for segment in raw_value.split(",") if segment.strip())

    if not tokens:
        return []

    parsed: list[EnumType] = []
    seen: set[EnumType] = set()
    invalid_values: list[str] = []

    for token in tokens:
        try:
            enum_member = enum_cls(token)
        except ValueError:
            invalid_values.append(token)
            continue

        if enum_member not in seen:
            parsed.append(enum_member)
            seen.add(enum_member)

    if invalid_values:
        valid_values = ", ".join(
            getattr(item, "value", str(item)) for item in enum_cls
        )
        raise ValueError(
            f"invalid value(s): {', '.join(invalid_values)}; valid values are: {valid_values}"
        )

    return parsed


__all__ = ["parse_bool_query_param", "parse_enum_list_query_param"]
