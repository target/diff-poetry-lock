from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import cast

import pytest

from diff_poetry_lock.utils import get_nested


@pytest.mark.parametrize(
    ("mapping", "keys", "expected", "description"),
    [
        ({"a": {"b": {"c": 1}}}, ["a", "b", "c"], 1, "simple nested dict"),
        ({"a": {"b": {}}}, ["a", "b", "c"], None, "missing final key returns None"),
        ({"a": {}}, ["a", "b", "c"], None, "missing intermediate key returns None"),
        ({"a": {"b": 2}}, ["a", "b", "c"], None, "intermediate value not mapping returns None"),
        ({"x": 1}, [], {"x": 1}, "empty keys returns original mapping object"),
        ({1: {2: 3}}, [1, 2], 3, "integer keys work"),
        ({None: {"a": 5}}, [None, "a"], 5, "None may be used as a key"),
        ({"a": {"c": 0, "d": False, "e": ""}}, ["a", "c"], 0, "falsy int value returned"),
        ({"a": {"c": 0, "d": False, "e": ""}}, ["a", "d"], False, "falsy bool value returned"),
        ({"a": {"c": 0, "d": False, "e": ""}}, ["a", "e"], "", "empty-string value returned"),
        (defaultdict(dict, {"a": {"b": 2}}), ["a", "b"], 2, "defaultdict (Mapping subclass) works"),
    ],
)
def test_get_nested_various(
    mapping: Mapping[object, object],
    keys: Iterable[object],
    expected: object,
    description: str,
) -> None:
    """Parametrized tests covering many get_nested scenarios."""

    # Use an iterator for one of the cases to ensure iterables work
    if description == "simple nested dict":
        # also test generator of keys
        gen = (k for k in ["a", "b", "c"])
        assert get_nested(mapping, gen) == expected

    result = get_nested(mapping, keys)

    if description == "empty keys returns original mapping object":
        # When keys is empty, result should be the same object we passed in
        assert result is mapping
    else:
        assert result == expected


def test_get_nested_with_non_mapping_root() -> None:
    """If the root object is not a Mapping, get_nested should return None."""

    root = 12345  # not a mapping
    # `get_nested` expects a Mapping type; cast here to silence mypy while
    # preserving the runtime behavior of passing a non-mapping root.
    assert get_nested(cast(Mapping[object, object], root), ["a"]) is None


def test_get_nested_with_generator_keys() -> None:
    """Generator of keys should be consumable by get_nested."""

    mapping = {"a": {"b": {"c": "final"}}}

    def key_gen() -> Iterable[str]:
        yield from ("a", "b", "c")

    assert get_nested(mapping, key_gen()) == "final"
