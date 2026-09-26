"""Utilities for working with text."""


def truncate_with_ellipsis(text: str, length: int) -> str:
    if len(text) > length:
        return text[:(length - 1)] + "\u2026"
    return text
