"""Bounded safe-YAML loader.

PyYAML's ``SafeLoader`` already prevents arbitrary code execution
through ``!!python/object`` and friends. The threat that remains is
denial-of-service via hostile files:

- alias expansion (``&a [*a, *a, *a, ...]``)
- unbounded depth
- unbounded key/sequence widths
- the YAML 1.1 "Norway problem" (the bare word ``no`` parses as
  Python ``False`` and other language-specific bool aliases leak
  into evidence)

This module wraps :class:`yaml.SafeLoader` with explicit limits
and exposes :func:`safe_load_yaml_bytes` for analyzer use.

Where the alias limit is enforced (LV-008)
==========================================

An alias is not a tag, so it has no constructor: PyYAML resolves
``*anchor`` in the **composer**, which turns the ``AliasEvent`` into a
second reference to the already-composed node before any constructor
runs. A limiter registered as a constructor for a made-up
``tag:yaml.org,2002:alias`` therefore never fires, which is how a
document with ``max_aliases=0`` used to parse an alias happily. The
limiter now overrides :meth:`yaml.composer.Composer.compose_node` and
counts the real ``AliasEvent`` occurrences as the composer consumes
them.

Counting alias events alone is not sufficient. Composition produces a
*graph*, not a tree: one anchored node reachable through several
aliases is expanded once per reference when the constructor walks it,
so a handful of aliases can still describe a structure with an
astronomical number of expanded nodes (the "billion laughs" shape).
Between composition and construction the loader therefore walks the
node graph once and enforces:

- no cycle (a self-referential anchor such as ``&a [*a]``, which
  would otherwise make construction and every downstream traversal
  non-terminating)
- a bounded number of distinct nodes
- a bounded number of *expanded* nodes, computed with saturating
  arithmetic so an over-budget graph is rejected without ever
  materialising the expansion
- the configured depth

The walk happens before ``construct_document``, so a rejected document
is never built in memory.
"""

from __future__ import annotations

from typing import Any

import yaml

# Default limits. They are deliberately conservative; tests can pass
# smaller values via the keyword arguments.
DEFAULT_MAX_BYTES = 4 * 1024 * 1024  # 4 MiB
DEFAULT_MAX_ALIASES = 64
DEFAULT_MAX_DEPTH = 32
DEFAULT_MAX_COLLECTION_ITEMS = 50_000
# Distinct nodes the composer may produce. Every node needs source
# bytes, so ``max_bytes`` already bounds this for an alias-free
# document; the limit is the explicit backstop.
DEFAULT_MAX_NODES = 1_000_000
# Nodes the document describes *after* alias expansion. Downstream
# traversal (depth and width checks, analyzers, evidence extraction)
# pays this cost, not the distinct-node cost, because PyYAML hands the
# same constructed object back for every alias. The budget is set
# above what 4 MiB of alias-free YAML can describe, so it fires only
# on genuine expansion - an alias graph whose raw alias count is
# modest but whose expansion is exponential.
DEFAULT_MAX_EXPANDED_NODES = 2_000_000

# YAML 1.1 booleans that should never appear in workflow evidence. We
# resolve them as plain strings to keep evidence deterministic.
_YAML_1_1_BOOL_LITERALS: frozenset[str] = frozenset(
    {
        "y",
        "Y",
        "yes",
        "Yes",
        "YES",
        "n",
        "N",
        "no",
        "No",
        "NO",
        "true",
        "True",
        "TRUE",
        "false",
        "False",
        "FALSE",
        "on",
        "On",
        "ON",
        "off",
        "Off",
        "OFF",
    }
)


class BoundedYamlError(ValueError):
    """Raised when YAML cannot be loaded under the configured limits."""


def _construct_string_no_yaml_1_1_bool(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    """Treat YAML 1.1 boolean literals (yes/no/on/off/true/false) as strings.

    This is defence in depth: most workflow evidence is already a
    quoted string, but if a contributor wrote ``on: yes`` we want
    ``yes`` to read as the string ``"yes"`` rather than Python
    ``True`` so stable finding keys do not change shape depending on
    the YAML parser's behaviour.
    """
    value = loader.construct_scalar(node)
    if value in _YAML_1_1_BOOL_LITERALS:
        return value
    return value


def _construct_bool_as_string(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    """Construct YAML 1.1 boolean scalars as plain strings.

    PyYAML's ``SafeLoader`` would otherwise map ``yes``/``on`` to
    Python ``True`` and ``no``/``off`` to ``False``. Stable finding
    keys depend on the textual value, so we override.
    """
    value = loader.construct_scalar(node)
    return str(value)


def _build_loader(max_aliases: int) -> type[yaml.SafeLoader]:
    """Return a :class:`SafeLoader` subclass that counts alias events.

    The subclass is built per load so the alias counter is never
    shared between documents.
    """

    class _BoundedSafeLoader(yaml.SafeLoader):
        """A ``SafeLoader`` that refuses to compose past the alias limit."""

        alias_limit = max_aliases

        def __init__(self, stream: Any) -> None:
            super().__init__(stream)
            self.alias_count = 0

        def compose_node(self, parent: Any, index: Any) -> Any:
            # ``compose_node`` is the single place PyYAML turns an
            # ``AliasEvent`` into a node reference. Counting here
            # counts real aliases - the thing the limit is named
            # after - rather than a tag that never resolves.
            if self.check_event(yaml.events.AliasEvent):
                self.alias_count += 1
                if self.alias_count > self.alias_limit:
                    raise BoundedYamlError(
                        f"YAML alias count exceeded limit of {self.alias_limit}."
                    )
            return super().compose_node(parent, index)

    _BoundedSafeLoader.add_constructor("tag:yaml.org,2002:str", _construct_string_no_yaml_1_1_bool)
    _BoundedSafeLoader.add_constructor("tag:yaml.org,2002:bool", _construct_bool_as_string)
    return _BoundedSafeLoader


def _iter_child_nodes(node: yaml.Node):
    """Yield the child nodes of a composed YAML node."""
    if isinstance(node, yaml.SequenceNode):
        yield from node.value
    elif isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            yield key_node
            yield value_node


def _check_node_graph(
    root: yaml.Node,
    *,
    max_depth: int,
    max_nodes: int,
    max_expanded_nodes: int,
) -> None:
    """Reject a composed node graph that is cyclic or over budget.

    The walk is iterative and memoised on node identity, so it costs
    O(nodes + edges) even when the *expanded* size it computes is
    astronomically larger. ``expanded`` and ``depth`` are accumulated
    with saturating arithmetic: once a subtree is over budget the
    exact value stops mattering and the traversal never tries to
    represent it.
    """
    expanded_cap = max_expanded_nodes + 1
    depth_cap = max_depth + 1
    # Keyed by ``id(node)``: every node is reachable from ``root`` for
    # the whole walk, so no identity can be recycled underneath us.
    expanded: dict[int, int] = {}
    depth_of: dict[int, int] = {}
    on_stack: set[int] = set()
    distinct = 0

    # (node, children_processed) - an explicit stack keeps a deep or
    # wide document from exhausting the interpreter's C stack.
    stack: list[tuple[yaml.Node, bool]] = [(root, False)]
    while stack:
        node, expanded_children = stack.pop()
        node_id = id(node)
        if not expanded_children:
            if node_id in expanded:
                continue
            if node_id in on_stack:
                raise BoundedYamlError(
                    "YAML document contains a recursive alias; refusing to expand it."
                )
            distinct += 1
            if distinct > max_nodes:
                raise BoundedYamlError(f"YAML document exceeds max_nodes={max_nodes}.")
            on_stack.add(node_id)
            stack.append((node, True))
            for child in _iter_child_nodes(node):
                stack.append((child, False))
            continue

        on_stack.discard(node_id)
        total = 1
        # ``depth`` counts container nesting the same way
        # :func:`_check_depth` does over the constructed object: a
        # leaf is 0 and a container is one deeper than its deepest
        # child, so the two checks agree on where the ceiling is.
        deepest_child = -1
        for child in _iter_child_nodes(node):
            total = min(total + expanded[id(child)], expanded_cap)
            deepest_child = max(deepest_child, depth_of[id(child)])
        expanded[node_id] = total
        depth_of[node_id] = min(deepest_child + 1, depth_cap) if deepest_child >= 0 else 0
        if total > max_expanded_nodes:
            raise BoundedYamlError(
                f"YAML alias expansion describes more than "
                f"{max_expanded_nodes} nodes; refusing to expand it."
            )
        if depth_of[node_id] > max_depth:
            raise BoundedYamlError(f"YAML depth exceeds max_depth={max_depth}.")


def safe_load_yaml_bytes(
    data: bytes,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_aliases: int = DEFAULT_MAX_ALIASES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_collection_items: int = DEFAULT_MAX_COLLECTION_ITEMS,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_expanded_nodes: int = DEFAULT_MAX_EXPANDED_NODES,
) -> Any:
    """Load ``data`` (bytes) as YAML with explicit safety limits.

    Raises :class:`BoundedYamlError` for any input that cannot be
    parsed safely. Aliases are counted as the composer consumes them
    and the composed graph is checked for cycles, node count,
    expansion, and depth *before* the document is constructed. The
    constructed structure is then checked for depth and width using
    the same algorithm as :mod:`app.utils.json_safe`.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise BoundedYamlError("YAML input must be bytes.")
    if len(data) > max_bytes:
        raise BoundedYamlError(f"YAML input exceeds max_bytes={max_bytes} (got {len(data)} bytes).")

    loader = _build_loader(max_aliases)(data)
    try:
        # ``get_single_node`` composes the document (counting alias
        # events) without constructing it; ``construct_document``
        # is the second half of what ``yaml.load`` does. Splitting
        # them is what lets the graph budget run before anything is
        # materialised.
        node = loader.get_single_node()
        if node is None:
            loaded = None
        else:
            _check_node_graph(
                node,
                max_depth=max_depth,
                max_nodes=max_nodes,
                max_expanded_nodes=max_expanded_nodes,
            )
            loaded = loader.construct_document(node)
    except yaml.YAMLError as exc:
        raise BoundedYamlError(f"Invalid YAML: {exc}") from exc
    finally:
        loader.dispose()

    _check_depth(loaded, depth=0, max_depth=max_depth)
    _check_collection_sizes(loaded, max_collection_items=max_collection_items)
    return loaded


def _check_depth(value: Any, *, depth: int, max_depth: int) -> None:
    if depth > max_depth:
        raise BoundedYamlError(f"YAML depth exceeds max_depth={max_depth}.")
    if isinstance(value, dict):
        for v in value.values():
            _check_depth(v, depth=depth + 1, max_depth=max_depth)
    elif isinstance(value, list):
        for v in value:
            _check_depth(v, depth=depth + 1, max_depth=max_depth)


def _check_collection_sizes(value: Any, *, max_collection_items: int) -> None:
    if isinstance(value, dict):
        if len(value) > max_collection_items:
            raise BoundedYamlError(
                f"YAML mapping has {len(value)} items; max is {max_collection_items}."
            )
        for v in value.values():
            _check_collection_sizes(v, max_collection_items=max_collection_items)
    elif isinstance(value, list):
        if len(value) > max_collection_items:
            raise BoundedYamlError(
                f"YAML sequence has {len(value)} items; max is {max_collection_items}."
            )
        for v in value:
            _check_collection_sizes(v, max_collection_items=max_collection_items)
