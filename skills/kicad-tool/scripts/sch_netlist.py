#!/usr/bin/env python3
"""Minimal KiCad ``.net`` netlist parser used by ``sch validate --baseline``.

Parses the S-expression-ish netlist that KiCad emits via ``kicad-cli sch
export netlist`` and exposes two helpers:

- :func:`parse_netlist` — read a ``.net`` file and return a dict mapping
  net name to a deterministically sorted list of ``{ref, pin, pin_function,
  pin_type}`` node dicts.
- :func:`diff_netlists` — compare two such dicts and return a structured diff
  (added / removed nets and per-net node deltas), ignoring KiCad's volatile
  net code numbers (it renumbers on every export).
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Tokenizer / parser (recursive descent over S-expressions)
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(" or ch == ")":
            tokens.append(ch)
            i += 1
            continue
        if ch == '"':
            # Quoted string with backslash escapes.
            j = i + 1
            buf: list[str] = []
            while j < n:
                c = text[j]
                if c == "\\" and j + 1 < n:
                    nxt = text[j + 1]
                    # Common escapes; default keeps the next char verbatim.
                    buf.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                    j += 2
                    continue
                if c == '"':
                    break
                buf.append(c)
                j += 1
            if j >= n:
                raise ValueError("unterminated string in netlist")
            tokens.append('"' + "".join(buf) + '"')
            i = j + 1
            continue
        # Bare atom (no quotes): up to whitespace or paren.
        j = i
        while j < n and not text[j].isspace() and text[j] not in "()":
            j += 1
        tokens.append(text[i:j])
        i = j
    return tokens


def _parse(tokens: list[str], pos: int) -> tuple[Any, int]:
    if pos >= len(tokens):
        raise ValueError("unexpected end of tokens")
    tok = tokens[pos]
    if tok == "(":
        pos += 1
        items: list[Any] = []
        while pos < len(tokens) and tokens[pos] != ")":
            node, pos = _parse(tokens, pos)
            items.append(node)
        if pos >= len(tokens):
            raise ValueError("missing closing paren")
        return items, pos + 1
    if tok == ")":
        raise ValueError("unexpected ')'")
    return tok, pos + 1


def _atom_value(tok: str) -> str:
    if isinstance(tok, str) and len(tok) >= 2 and tok.startswith('"') and tok.endswith('"'):
        return tok[1:-1]
    return tok


def _is_list_with_head(node: Any, head: str) -> bool:
    return isinstance(node, list) and node and isinstance(node[0], str) and node[0] == head


def _find_child(node: list[Any], head: str) -> list[Any] | None:
    for child in node[1:]:
        if _is_list_with_head(child, head):
            return child
    return None


def _child_value(node: list[Any], head: str) -> str | None:
    child = _find_child(node, head)
    if child is None or len(child) < 2:
        return None
    return _atom_value(child[1])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_netlist(path: str) -> dict[str, list[dict[str, str]]]:
    """Parse a KiCad netlist and return ``{net_name: [node_dict, ...]}``.

    Each node dict contains ``ref``, ``pin``, ``pin_function``, ``pin_type``
    (missing fields are returned as empty strings). Nodes within each net are
    sorted by ``(ref, pin)`` for deterministic comparison.
    """
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    tokens = _tokenize(text)
    tree, _ = _parse(tokens, 0)
    if not isinstance(tree, list):
        raise ValueError("netlist root is not a list")

    nets_block = _find_child(tree, "nets")
    if nets_block is None:
        return {}

    result: dict[str, list[dict[str, str]]] = {}
    for net in nets_block[1:]:
        if not _is_list_with_head(net, "net"):
            continue
        name = _child_value(net, "name") or ""
        nodes: list[dict[str, str]] = []
        for child in net[1:]:
            if not _is_list_with_head(child, "node"):
                continue
            nodes.append(
                {
                    "ref": _child_value(child, "ref") or "",
                    "pin": _child_value(child, "pin") or "",
                    "pin_function": _child_value(child, "pin_function") or "",
                    "pin_type": _child_value(child, "pin_type") or "",
                }
            )
        nodes.sort(key=lambda n: (n["ref"], n["pin"]))
        result[name] = nodes
    return result


def _node_key(node: dict[str, str]) -> tuple[str, str]:
    return (node.get("ref", ""), node.get("pin", ""))


def diff_netlists(
    a: dict[str, list[dict[str, str]]],
    b: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    """Diff two parsed netlists, ignoring net code numbers.

    Returns ``{"added_nets": [...], "removed_nets": [...],
    "changed_nets": {netname: {"added_nodes": [...], "removed_nodes": [...]}}}``.
    Net membership is compared by ``(ref, pin)``; pin_function / pin_type
    differences on the same ref+pin are also surfaced via a paired
    removed+added entry so reviewers can spot them.
    """
    a_names = set(a)
    b_names = set(b)
    added_nets = sorted(b_names - a_names)
    removed_nets = sorted(a_names - b_names)

    changed: dict[str, dict[str, list[dict[str, str]]]] = {}
    for name in sorted(a_names & b_names):
        a_nodes = {_node_key(n): n for n in a[name]}
        b_nodes = {_node_key(n): n for n in b[name]}
        added_keys = sorted(b_nodes.keys() - a_nodes.keys())
        removed_keys = sorted(a_nodes.keys() - b_nodes.keys())
        added_nodes = [b_nodes[k] for k in added_keys]
        removed_nodes = [a_nodes[k] for k in removed_keys]
        # Surface metadata-only changes (same ref+pin, different pin_function /
        # pin_type) as a paired remove+add so they don't silently disappear.
        for k in sorted(a_nodes.keys() & b_nodes.keys()):
            if a_nodes[k] != b_nodes[k]:
                removed_nodes.append(a_nodes[k])
                added_nodes.append(b_nodes[k])
        if added_nodes or removed_nodes:
            added_nodes.sort(key=_node_key)
            removed_nodes.sort(key=_node_key)
            changed[name] = {
                "added_nodes": added_nodes,
                "removed_nodes": removed_nodes,
            }

    return {
        "added_nets": added_nets,
        "removed_nets": removed_nets,
        "changed_nets": changed,
    }
