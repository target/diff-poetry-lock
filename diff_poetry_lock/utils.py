from collections.abc import Iterable, Mapping
from functools import reduce
from typing import TypeVar, cast

Key = TypeVar("Key")


def get_nested(
    d: Mapping[Key, object],
    keys: Iterable[Key],
) -> object | None:
    return reduce(
        lambda current, key: current.get(key) if isinstance(current, Mapping) else None,
        keys,
        cast(object, d),
    )
