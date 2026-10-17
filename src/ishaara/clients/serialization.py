"""Turning domain values into JSON-safe structures for document and JSON-column storage."""

from typing import Any

from pydantic import TypeAdapter

_ANY_DICT = TypeAdapter(dict[str, Any])


def to_jsonable(fields: dict[str, Any]) -> dict[str, Any]:
    """Convert a dict of domain values (enums, datetimes, models) to JSON-safe values.

    Args:
        fields: Field names mapped to domain values.

    Returns:
        The same keys with every value converted to plain JSON types.
    """
    jsonable: dict[str, Any] = _ANY_DICT.dump_python(fields, mode="json")
    return jsonable
