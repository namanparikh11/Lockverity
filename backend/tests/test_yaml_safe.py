"""Tests for :mod:`app.utils.yaml_safe`."""

from __future__ import annotations

from unittest import mock

import pytest
import yaml
from app.utils.yaml_safe import (
    DEFAULT_MAX_ALIASES,
    BoundedYamlError,
    safe_load_yaml_bytes,
)


def test_parses_basic_mapping() -> None:
    out = safe_load_yaml_bytes(b"a: 1\nb: 2\n")
    assert out == {"a": 1, "b": 2}


def test_parses_nested_sequence() -> None:
    out = safe_load_yaml_bytes(b"- 1\n- 2\n- [3, 4]\n")
    assert out == [1, 2, [3, 4]]


def test_rejects_oversize_input() -> None:
    big = b"a: 1\n" * 200_000
    with pytest.raises(BoundedYamlError):
        safe_load_yaml_bytes(big, max_bytes=8 * 192)


def test_rejects_oversize_default_input() -> None:
    big = b"a: 1\n" * 1_000_000  # > 4 MiB
    with pytest.raises(BoundedYamlError):
        safe_load_yaml_bytes(big)


def test_rejects_alias_bomb() -> None:
    payload = b"a: &x ['x','x','x','x','x','x','x','x','x','x']\n" * 200
    with pytest.raises(BoundedYamlError):
        safe_load_yaml_bytes(payload, max_aliases=10)


def test_rejects_excessive_depth() -> None:
    deep = b"a: " * 200 + b"1\n"
    with pytest.raises(BoundedYamlError):
        safe_load_yaml_bytes(deep, max_depth=10)


def test_rejects_huge_collection() -> None:
    items = ",".join(f'"{i}"' for i in range(500))
    with pytest.raises(BoundedYamlError):
        safe_load_yaml_bytes(f"[{items}]", max_collection_items=100)


def test_rejects_non_bytes() -> None:
    with pytest.raises(BoundedYamlError):
        safe_load_yaml_bytes("a: 1")  # type: ignore[arg-type]


def test_yaml_1_1_bool_literals_preserved_as_strings() -> None:
    out = safe_load_yaml_bytes(b"flag_yes: yes\nflag_no: no\nflag_on: on\n")
    assert out == {"flag_yes": "yes", "flag_no": "no", "flag_on": "on"}


def test_yaml_unfamiliar_construct_raises() -> None:
    bad = b"!!python/object/apply:os.system ['echo hi']\n"
    with pytest.raises(BoundedYamlError):
        safe_load_yaml_bytes(bad)


# ---------------------------------------------------------------------------
# LV-008: the alias limit counts real alias events
# ---------------------------------------------------------------------------
# The historical limiter registered a constructor for a
# ``tag:yaml.org,2002:alias`` tag. No such tag exists - PyYAML resolves
# an alias in the composer, before any constructor runs - so the
# limiter never fired and ``max_aliases=0`` happily parsed an alias.

_ONE_ALIAS = b"anchor: &a [1, 2]\nfirst: *a\n"
_TWO_ALIASES = b"anchor: &a [1, 2]\nfirst: *a\nsecond: *a\n"
_THREE_ALIASES = b"anchor: &a [1, 2]\nfirst: *a\nsecond: *a\nthird: *a\n"


def test_zero_aliases_allowed_rejects_one_alias() -> None:
    with pytest.raises(BoundedYamlError) as exc:
        safe_load_yaml_bytes(_ONE_ALIAS, max_aliases=0)
    assert "alias" in str(exc.value).lower()


def test_alias_count_at_the_limit_is_accepted() -> None:
    assert safe_load_yaml_bytes(_TWO_ALIASES, max_aliases=2) == {
        "anchor": [1, 2],
        "first": [1, 2],
        "second": [1, 2],
    }


def test_alias_count_one_past_the_limit_is_rejected() -> None:
    with pytest.raises(BoundedYamlError):
        safe_load_yaml_bytes(_THREE_ALIASES, max_aliases=2)


def test_anchor_without_an_alias_costs_nothing() -> None:
    """An anchor is only a label; the limit is on dereferences."""
    assert safe_load_yaml_bytes(b"anchor: &a 1\n", max_aliases=0) == {"anchor": 1}


# The classic expansion shape. Each level references the one below it
# nine times, so the alias *count* stays modest while the expanded
# node count grows by roughly 9x per level.
def _expansion_bomb(levels: int) -> bytes:
    lines = [b'l0: &l0 ["x","x","x","x","x","x","x","x","x"]']
    for level in range(1, levels):
        refs = ",".join([f"*l{level - 1}"] * 9)
        lines.append(f"l{level}: &l{level} [{refs}]".encode())
    return b"\n".join(lines) + b"\n"


def test_expansive_alias_graph_is_rejected_under_default_limits() -> None:
    """54 aliases is under ``DEFAULT_MAX_ALIASES``; the expansion is not.

    This is the case the alias counter alone cannot catch: the raw
    alias count looks reasonable, but the graph describes millions of
    nodes once every reference is walked.
    """
    payload = _expansion_bomb(7)
    assert payload.count(b"*") == 54 < DEFAULT_MAX_ALIASES
    with pytest.raises(BoundedYamlError) as exc:
        safe_load_yaml_bytes(payload)
    assert "expansion" in str(exc.value)


def test_expansion_budget_is_configurable() -> None:
    payload = _expansion_bomb(4)
    with pytest.raises(BoundedYamlError):
        safe_load_yaml_bytes(payload, max_aliases=100, max_expanded_nodes=100)
    # The same document loads once the budget covers it.
    loaded = safe_load_yaml_bytes(
        payload, max_aliases=100, max_expanded_nodes=100_000, max_collection_items=100_000
    )
    assert isinstance(loaded, dict)


def test_expansion_check_runs_before_construction() -> None:
    """The rejection must not depend on building the document first.

    A constructor that ran would be visible as a call on the loader;
    instead the graph walk raises while the document is still a node
    tree, which is what keeps the expansion out of memory.
    """
    constructed: list[object] = []
    real_construct = yaml.SafeLoader.construct_document

    def _spy(self, node):  # pragma: no cover - must never run
        constructed.append(node)
        return real_construct(self, node)

    with (
        mock.patch.object(yaml.SafeLoader, "construct_document", _spy),
        pytest.raises(BoundedYamlError),
    ):
        safe_load_yaml_bytes(_expansion_bomb(7))
    assert constructed == []


@pytest.mark.parametrize(
    "payload",
    [
        b"a: &x [*x]\n",
        b"&root\nself: *root\n",
        b"a: &x {b: *x}\n",
        b"a: &x [1, [2, *x]]\n",
    ],
)
def test_recursive_alias_structures_are_rejected(payload: bytes) -> None:
    """A self-referential anchor must not become an infinite structure."""
    with pytest.raises(BoundedYamlError) as exc:
        safe_load_yaml_bytes(payload, max_aliases=100)
    assert "recursive" in str(exc.value)


def test_ordinary_github_actions_workflow_still_parses() -> None:
    workflow = b"""
name: CI
on:
  push:
    branches: [main]
  pull_request:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: python -m pytest
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python -m ruff check .
"""
    loaded = safe_load_yaml_bytes(workflow)
    assert loaded["name"] == "CI"
    assert set(loaded["jobs"]) == {"build", "lint"}
    assert loaded["jobs"]["build"]["steps"][0]["uses"] == "actions/checkout@v4"


def test_workflow_with_reasonable_anchors_still_parses() -> None:
    """Anchors are a normal YAML feature; a handful must keep working."""
    workflow = b"""
defaults: &defaults
  runs-on: ubuntu-latest
  timeout-minutes: 10
jobs:
  build:
    <<: *defaults
    steps:
      - run: make build
  test:
    <<: *defaults
    steps:
      - run: make test
"""
    loaded = safe_load_yaml_bytes(workflow)
    assert loaded["jobs"]["build"]["runs-on"] == "ubuntu-latest"
    assert loaded["jobs"]["test"]["timeout-minutes"] == 10
