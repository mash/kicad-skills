"""Pure S-expression text mutation for KiCad 10 schematic files.

All functions take a path to a `.kicad_sch` file, perform a structural edit on
the raw text (preserving tab indentation and existing formatting), and return a
result dict of the form::

    {
        "action": "<verb_element>",
        "changed": bool,
        "diff": "<unified diff string>",
        "details": {...},
    }

When ``dry_run=True`` no file is written. When ``dry_run=False`` and the edit
changes the text, the file is overwritten with the new content.

Notes
-----
- We deliberately do NOT round-trip via kiutils for writes — kiutils can lose
  formatting / reorder tokens / re-emit UUIDs. kiutils is only used to look up
  anchors (e.g., a symbol's current ``(at)`` position).
- Block extraction is done by paren-balancing on the raw text starting from a
  located opening token. This works for KiCad 10's pretty-printed format where
  every `(symbol ...)`, `(wire ...)`, `(junction ...)`, `(global_label ...)`,
  `(hierarchical_label ...)`, `(label ...)` block sits at depth 1 with leading
  tab.
- Coordinates are rendered with KiCad's usual style: integer if value is an
  integer, else with the minimum number of decimals KiCad would emit. We use
  ``_fmt_coord`` to mimic this.
"""

from __future__ import annotations

import difflib
import math
import re
import sys
import uuid as _uuid
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, "/Users/mash/src/github.com/mvnmgrx/kiutils/src")

from kiutils.schematic import Schematic  # noqa: E402

from kicad_sch_bbox_collisions import local_to_schematic  # noqa: E402


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _fmt_coord(v: float) -> str:
    """Format a coordinate the way KiCad does: drop trailing zeros, no exponent."""
    if isinstance(v, int):
        return str(v)
    # Round to 4 decimals (KiCad uses up to 4 in practice for sch coords).
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _new_uuid() -> str:
    return str(_uuid.uuid4())


def _read(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _diff(old: str, new: str, path: Path) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path),
            n=3,
        )
    )


def _maybe_write(path: Path, old: str, new: str, dry_run: bool) -> bool:
    """Write file if not dry-run and content changed. Returns True if changed."""
    if old == new:
        return False
    if not dry_run:
        Path(path).write_text(new, encoding="utf-8")
    return True


def _find_block_end(text: str, open_idx: int) -> int:
    """Given index of an `(`, return the index just past the matching `)`.

    Respects double-quoted strings (with `\"` escapes).
    """
    if text[open_idx] != "(":
        raise ValueError(f"expected '(' at {open_idx}, got {text[open_idx]!r}")
    depth = 0
    i = open_idx
    n = len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced parentheses")


def _expand_block_with_indent(text: str, open_idx: int, end_idx: int) -> tuple[int, int]:
    """Expand the block range to include leading tabs/spaces and the trailing newline.

    Returns ``(start, stop)`` where ``text[start:stop]`` is the full block with
    its indentation prefix and trailing ``\\n`` (if any).
    """
    # walk back to start of line
    start = open_idx
    while start > 0 and text[start - 1] in (" ", "\t"):
        start -= 1
    # include trailing newline
    stop = end_idx
    if stop < len(text) and text[stop] == "\n":
        stop += 1
    return start, stop


def _detect_indent_unit(text: str) -> str:
    """Heuristically detect indentation unit: tab if a tab-indented block exists."""
    if re.search(r"^\t\(", text, flags=re.MULTILINE):
        return "\t"
    if re.search(r"^    \(", text, flags=re.MULTILINE):
        return "    "
    return "\t"


# ---------------------------------------------------------------------------
# Block locators
# ---------------------------------------------------------------------------


_TOKEN_AT_DEPTH1_RE = re.compile(r"^\t\(([a-z_]+)\b", re.MULTILINE)


def _iter_top_blocks(text: str, token: str):
    """Yield (open_idx, end_idx) for each ``(token ...)`` block at depth 1 (tab indent)."""
    pat = re.compile(r"^\t\(" + re.escape(token) + r"\b", re.MULTILINE)
    for m in pat.finditer(text):
        open_idx = m.start() + 1  # skip the leading tab
        end_idx = _find_block_end(text, open_idx)
        yield open_idx, end_idx


def _block_field(block: str, field: str) -> str | None:
    """Extract the first ``(field ...)`` inner content from a block string."""
    m = re.search(r"\(" + re.escape(field) + r"\s+([^)]*)\)", block)
    if not m:
        return None
    return m.group(1).strip()


def _block_uuid(block: str) -> str | None:
    m = re.search(r'\(uuid\s+"([^"]+)"\)', block)
    return m.group(1) if m else None


def _block_at(block: str) -> tuple[float, float, float] | None:
    m = re.search(r"\(at\s+(-?[\d.]+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?\s*\)", block)
    if not m:
        return None
    x, y = float(m.group(1)), float(m.group(2))
    rot = float(m.group(3)) if m.group(3) is not None else 0.0
    return x, y, rot


def _coords_close(a: float, b: float, tol: float = 0.001) -> bool:
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
# move_symbol
# ---------------------------------------------------------------------------


def _find_symbol_block_by_ref(text: str, ref: str) -> tuple[int, int, str] | None:
    """Locate a top-level (symbol ...) block whose Reference property == ref.

    Returns (open_idx, end_idx, block_text) or None.
    """
    for open_idx, end_idx in _iter_top_blocks(text, "symbol"):
        block = text[open_idx:end_idx]
        m = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', block)
        if m and m.group(1) == ref:
            return open_idx, end_idx, block
    return None


def move_symbol(
    sch_path: str | Path,
    ref: str,
    to: tuple[float, float],
    rotation: float | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Move a symbol by reference, cascading all five property ``(at)`` positions.

    Returns ``details`` containing old/new symbol position and absolute pin
    coordinates (computed via lib pin offsets).
    """
    sch_path = Path(sch_path)
    text = _read(sch_path)

    found = _find_symbol_block_by_ref(text, ref)
    if not found:
        raise ValueError(f"symbol with reference {ref!r} not found in {sch_path}")
    open_idx, end_idx, block = found

    sym_at = _block_at(block)
    if sym_at is None:
        raise ValueError(f"symbol {ref!r} has no (at ...) field")
    old_x, old_y, old_rot = sym_at
    new_x, new_y = float(to[0]), float(to[1])
    new_rot = float(rotation) if rotation is not None else old_rot
    dx = new_x - old_x
    dy = new_y - old_y
    drot = new_rot - old_rot

    # Replace the symbol's own (at ...) — must replace ONLY the first one inside the block.
    sym_at_re = re.compile(r"\(at\s+-?[\d.]+\s+-?[\d.]+(?:\s+-?[\d.]+)?\s*\)")
    m_sym_at = sym_at_re.search(block)
    if not m_sym_at:
        raise ValueError("symbol (at ...) not found")
    new_sym_at = f"(at {_fmt_coord(new_x)} {_fmt_coord(new_y)} {_fmt_coord(new_rot)})"
    new_block = block[: m_sym_at.start()] + new_sym_at + block[m_sym_at.end() :]

    # Cascade property (at ...): shift each by (dx, dy) and add drot to its rotation.
    prop_re = re.compile(
        r'(\(property\s+"(?:Reference|Value|Footprint|Datasheet|Description)"\s+"[^"]*"\s*\n\s*)'
        r"\(at\s+(-?[\d.]+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?\s*\)"
    )

    def _shift(m: re.Match) -> str:
        head = m.group(1)
        px = float(m.group(2)) + dx
        py = float(m.group(3)) + dy
        prot = float(m.group(4) or "0") + drot
        return f"{head}(at {_fmt_coord(px)} {_fmt_coord(py)} {_fmt_coord(prot)})"

    new_block = prop_re.sub(_shift, new_block)

    new_text = text[:open_idx] + new_block + text[end_idx:]

    # Compute new absolute pin coordinates using lib symbol pin offsets via kiutils.
    pins_abs: list[dict[str, Any]] = []
    try:
        sch_obj = Schematic.from_file(str(sch_path))
        lib = {s.entryName: s for s in sch_obj.libSymbols}
        sym_obj = next(
            (
                s
                for s in sch_obj.schematicSymbols
                if any(p.key == "Reference" and p.value == ref for p in s.properties)
            ),
            None,
        )
        if sym_obj is not None:
            libsym = lib.get(sym_obj.entryName)
            if libsym is not None:
                for unit in getattr(libsym, "units", []):
                    for pin in getattr(unit, "pins", []):
                        ax, ay = local_to_schematic(
                            new_x, new_y, int(new_rot) % 360, pin.position.X, pin.position.Y
                        )
                        pins_abs.append(
                            {
                                "number": pin.number,
                                "name": pin.name,
                                "x": round(ax, 4),
                                "y": round(ay, 4),
                            }
                        )
    except Exception as exc:  # noqa: BLE001
        pins_abs = [{"error": f"pin computation failed: {exc}"}]

    changed = _maybe_write(sch_path, text, new_text, dry_run)
    return {
        "action": "move_symbol",
        "changed": changed,
        "diff": _diff(text, new_text, sch_path),
        "details": {
            "ref": ref,
            "old": {"x": old_x, "y": old_y, "rotation": old_rot},
            "new": {"x": new_x, "y": new_y, "rotation": new_rot},
            "delta": {"dx": dx, "dy": dy, "drot": drot},
            "pins": pins_abs,
        },
    }


def move_symbol_property(
    sch_path: str | Path,
    ref: str,
    key: str,
    to: tuple[float, float],
    rotation: float | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Move a single symbol property field's placement by reference.

    Only standard KiCad property keys are supported (Reference/Value/etc).
    The command errors if the property exists but has no `(at ...)` placement.
    """
    allowed_keys = {"Reference", "Value", "Footprint", "Datasheet", "Description"}
    if key not in allowed_keys:
        raise ValueError(f"key must be one of {sorted(allowed_keys)}, got {key!r}")

    sch_path = Path(sch_path)
    text = _read(sch_path)

    found = _find_symbol_block_by_ref(text, ref)
    if not found:
        raise ValueError(f"symbol with reference {ref!r} not found in {sch_path}")
    sym_open_idx, sym_end_idx, sym_block = found

    prop_head_pat = re.compile(r'\(property\s+"' + re.escape(key) + r'"\s+"[^"]*"')
    m_head = prop_head_pat.search(sym_block)
    if not m_head:
        raise ValueError(f"property {key!r} not found on symbol {ref!r}")

    prop_open_rel = m_head.start()
    prop_end_rel = _find_block_end(sym_block, prop_open_rel)
    prop_block = sym_block[prop_open_rel:prop_end_rel]

    # Find current (at ...) and whether rotation is explicitly present.
    at_pat = re.compile(r"\(at\s+(-?[\d.]+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?\s*\)")
    m_at = at_pat.search(prop_block)
    if not m_at:
        raise ValueError(f"property {key!r} on symbol {ref!r} has no (at ...) placement")

    old_x, old_y = float(m_at.group(1)), float(m_at.group(2))
    old_rot_raw = m_at.group(3)
    had_rot = old_rot_raw is not None
    old_rot = float(old_rot_raw) if old_rot_raw is not None else 0.0

    new_x, new_y = float(to[0]), float(to[1])
    new_rot = float(rotation) if rotation is not None else old_rot

    # Preserve the original "(at x y)" form when rotation arg is omitted.
    if rotation is None and not had_rot:
        new_at = f"(at {_fmt_coord(new_x)} {_fmt_coord(new_y)})"
    else:
        new_at = f"(at {_fmt_coord(new_x)} {_fmt_coord(new_y)} {_fmt_coord(new_rot)})"

    prop_at_re = re.compile(
        r"\(at\s+-?[\d.]+\s+-?[\d.]+(?:\s+-?[\d.]+)?\s*\)",
    )
    m_sub = prop_at_re.search(prop_block)
    if not m_sub:
        # Should not happen because we already verified (at ...), but keep error explicit.
        raise ValueError(f"property {key!r} on symbol {ref!r} has no (at ...) placement")

    new_prop_block = prop_at_re.sub(new_at, prop_block, count=1)
    new_sym_block = sym_block[:prop_open_rel] + new_prop_block + sym_block[prop_end_rel:]
    new_text = text[:sym_open_idx] + new_sym_block + text[sym_end_idx:]

    changed = _maybe_write(sch_path, text, new_text, dry_run)
    dx = new_x - old_x
    dy = new_y - old_y
    drot = new_rot - old_rot

    return {
        "action": "move_symbol_property",
        "changed": changed,
        "diff": _diff(text, new_text, sch_path),
        "details": {
            "ref": ref,
            "key": key,
            "old": {"x": old_x, "y": old_y, "rotation": old_rot, "had_rotation": had_rot},
            "new": {"x": new_x, "y": new_y, "rotation": new_rot, "had_rotation": rotation is not None or had_rot},
            "delta": {"dx": dx, "dy": dy, "drot": drot},
        },
    }


# ---------------------------------------------------------------------------
# add_wire / delete_wire
# ---------------------------------------------------------------------------


def _wire_block(x1: float, y1: float, x2: float, y2: float, wire_type: str, uuid_str: str) -> str:
    return (
        f"\t(wire\n"
        f"\t\t(pts\n"
        f"\t\t\t(xy {_fmt_coord(x1)} {_fmt_coord(y1)}) (xy {_fmt_coord(x2)} {_fmt_coord(y2)})\n"
        f"\t\t)\n"
        f"\t\t(stroke\n"
        f"\t\t\t(width 0)\n"
        f"\t\t\t(type {wire_type})\n"
        f"\t\t)\n"
        f'\t\t(uuid "{uuid_str}")\n'
        f"\t)\n"
    )


def add_wire(
    sch_path: str | Path,
    pt_from: tuple[float, float],
    pt_to: tuple[float, float],
    wire_type: str = "default",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Append a (wire ...) block. Inserts after the last existing wire if any,
    otherwise just before the closing top-level paren.
    """
    sch_path = Path(sch_path)
    text = _read(sch_path)
    if wire_type not in {"default", "solid"}:
        raise ValueError(f"wire_type must be 'default' or 'solid', got {wire_type!r}")

    uuid_str = _new_uuid()
    block = _wire_block(pt_from[0], pt_from[1], pt_to[0], pt_to[1], wire_type, uuid_str)

    # Insert after the last existing top-level wire block, if any.
    last_end: int | None = None
    for open_idx, end_idx in _iter_top_blocks(text, "wire"):
        last_end = end_idx
    if last_end is not None:
        # consume trailing newline
        insert_at = last_end
        if insert_at < len(text) and text[insert_at] == "\n":
            insert_at += 1
        new_text = text[:insert_at] + block + text[insert_at:]
    else:
        # fall back: insert before the final closing paren of the file
        last_paren = text.rstrip().rfind(")")
        if last_paren < 0:
            raise ValueError("could not locate end of schematic")
        new_text = text[:last_paren] + block + text[last_paren:]

    changed = _maybe_write(sch_path, text, new_text, dry_run)
    return {
        "action": "add_wire",
        "changed": changed,
        "diff": _diff(text, new_text, sch_path),
        "details": {
            "uuid": uuid_str,
            "from": list(pt_from),
            "to": list(pt_to),
            "type": wire_type,
        },
    }


def _wire_endpoints(block: str) -> tuple[tuple[float, float], tuple[float, float]] | None:
    m = re.search(
        r"\(xy\s+(-?[\d.]+)\s+(-?[\d.]+)\)\s+\(xy\s+(-?[\d.]+)\s+(-?[\d.]+)\)",
        block,
    )
    if not m:
        return None
    return (float(m.group(1)), float(m.group(2))), (float(m.group(3)), float(m.group(4)))


def delete_wire(
    sch_path: str | Path,
    uuid: str | None = None,
    pt_from: tuple[float, float] | None = None,
    pt_to: tuple[float, float] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a (wire ...) block by uuid or matching endpoints (order-insensitive)."""
    sch_path = Path(sch_path)
    text = _read(sch_path)
    if uuid is None and (pt_from is None or pt_to is None):
        raise ValueError("delete_wire requires either uuid or both pt_from and pt_to")

    target: tuple[int, int] | None = None
    matched_uuid: str | None = None
    matched_pts = None
    for open_idx, end_idx in _iter_top_blocks(text, "wire"):
        block = text[open_idx:end_idx]
        wuuid = _block_uuid(block)
        if uuid is not None and wuuid == uuid:
            target = (open_idx, end_idx)
            matched_uuid = wuuid
            matched_pts = _wire_endpoints(block)
            break
        if uuid is None:
            pts = _wire_endpoints(block)
            if pts is None:
                continue
            (a, b) = pts
            ok = (
                _coords_close(a[0], pt_from[0])
                and _coords_close(a[1], pt_from[1])
                and _coords_close(b[0], pt_to[0])
                and _coords_close(b[1], pt_to[1])
            ) or (
                _coords_close(a[0], pt_to[0])
                and _coords_close(a[1], pt_to[1])
                and _coords_close(b[0], pt_from[0])
                and _coords_close(b[1], pt_from[1])
            )
            if ok:
                target = (open_idx, end_idx)
                matched_uuid = wuuid
                matched_pts = pts
                break

    if target is None:
        raise ValueError("no matching wire found")

    start, stop = _expand_block_with_indent(text, target[0], target[1])
    new_text = text[:start] + text[stop:]
    changed = _maybe_write(sch_path, text, new_text, dry_run)
    return {
        "action": "delete_wire",
        "changed": changed,
        "diff": _diff(text, new_text, sch_path),
        "details": {
            "uuid": matched_uuid,
            "endpoints": matched_pts,
        },
    }


# ---------------------------------------------------------------------------
# Labels: add / move / delete
# ---------------------------------------------------------------------------


_LABEL_TOKENS = {
    "global": "global_label",
    "hier": "hierarchical_label",
    "local": "label",
}


def _find_sibling_label_block(text: str, kind_token: str) -> tuple[int, int, str] | None:
    for open_idx, end_idx in _iter_top_blocks(text, kind_token):
        return open_idx, end_idx, text[open_idx:end_idx]
    return None


def add_label(
    sch_path: str | Path,
    kind: str,
    name: str,
    at: tuple[float, float],
    rotation: float = 0.0,
    justify: str | None = None,  # noqa: ARG001 — kept for API parity; cloned from sibling
    dry_run: bool = False,
) -> dict[str, Any]:
    """Add a label by cloning a sibling block of the same kind.

    Per the KiCad workflow rules, we never synthesize ``(justify ...)``,
    ``(effects ...)``, or related quirks from scratch — we copy from a sibling
    in the same file and only swap text/at/uuid.

    The ``justify`` argument is currently ignored; the cloned sibling's justify
    is preserved. Pass it explicitly only if a future implementation overrides.
    """
    sch_path = Path(sch_path)
    text = _read(sch_path)
    if kind not in _LABEL_TOKENS:
        raise ValueError(f"kind must be one of {sorted(_LABEL_TOKENS)}, got {kind!r}")
    token = _LABEL_TOKENS[kind]

    sib = _find_sibling_label_block(text, token)
    if sib is None:
        raise ValueError(
            f"no existing {token!r} block in {sch_path} to clone from; "
            "add one manually first or pick a different kind"
        )
    _, sib_end, sib_block = sib

    new_uuid = _new_uuid()
    x, y = float(at[0]), float(at[1])
    rot = float(rotation)

    # Replace the head: ({token} "OLDNAME" → ({token} "NEWNAME"
    new_block = re.sub(
        r'^\(' + re.escape(token) + r'\s+"[^"]*"',
        f'({token} "{name}"',
        sib_block,
        count=1,
    )
    # Replace the first (at ...) — that's the label's own position.
    new_block = re.sub(
        r"\(at\s+-?[\d.]+\s+-?[\d.]+(?:\s+-?[\d.]+)?\s*\)",
        f"(at {_fmt_coord(x)} {_fmt_coord(y)} {_fmt_coord(rot)})",
        new_block,
        count=1,
    )
    # Replace label uuid (the first uuid in the block — the label's, before Intersheetrefs).
    new_block = re.sub(
        r'\(uuid\s+"[^"]+"\)',
        f'(uuid "{new_uuid}")',
        new_block,
        count=1,
    )
    # If global label has Intersheetrefs (at ...), align it to label position.
    if token == "global_label":
        new_block = re.sub(
            r'(\(property\s+"Intersheetrefs"[^\n]*\n\s*)\(at\s+-?[\d.]+\s+-?[\d.]+(?:\s+-?[\d.]+)?\s*\)',
            lambda m: f"{m.group(1)}(at {_fmt_coord(x)} {_fmt_coord(y)} {_fmt_coord(rot)})",
            new_block,
            count=1,
        )

    # Insert immediately after the sibling block (preserving its trailing newline).
    insert_at = sib_end
    if insert_at < len(text) and text[insert_at] == "\n":
        insert_at += 1
    # The sibling block we extracted does NOT include the leading tab; we need to
    # prepend the tab before our cloned block since sib_block was extracted from
    # the depth-1 token onward without the leading tab.
    new_block_with_indent = "\t" + new_block + "\n"
    new_text = text[:insert_at] + new_block_with_indent + text[insert_at:]

    changed = _maybe_write(sch_path, text, new_text, dry_run)
    return {
        "action": "add_label",
        "changed": changed,
        "diff": _diff(text, new_text, sch_path),
        "details": {
            "kind": kind,
            "name": name,
            "at": [x, y],
            "rotation": rot,
            "uuid": new_uuid,
        },
    }


def _find_label_block_by_uuid(text: str, uuid: str) -> tuple[str, int, int, str] | None:
    """Search all label kinds for a block whose first uuid matches.

    Returns (token, open_idx, end_idx, block) or None.
    """
    for kind, token in _LABEL_TOKENS.items():  # noqa: B007
        for open_idx, end_idx in _iter_top_blocks(text, token):
            block = text[open_idx:end_idx]
            m = re.search(r'\(uuid\s+"([^"]+)"\)', block)
            if m and m.group(1) == uuid:
                return token, open_idx, end_idx, block
    return None


def move_label(
    sch_path: str | Path,
    uuid: str,
    to: tuple[float, float],
    rotation: float | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Move a label by uuid; updates the label's (at) and any Intersheetrefs (at)."""
    sch_path = Path(sch_path)
    text = _read(sch_path)
    found = _find_label_block_by_uuid(text, uuid)
    if not found:
        raise ValueError(f"label with uuid {uuid!r} not found")
    token, open_idx, end_idx, block = found

    old_at = _block_at(block)
    if old_at is None:
        raise ValueError("label has no (at ...) field")
    old_x, old_y, old_rot = old_at
    new_x, new_y = float(to[0]), float(to[1])
    new_rot = float(rotation) if rotation is not None else old_rot

    # Replace the first (at ...)
    new_block = re.sub(
        r"\(at\s+-?[\d.]+\s+-?[\d.]+(?:\s+-?[\d.]+)?\s*\)",
        f"(at {_fmt_coord(new_x)} {_fmt_coord(new_y)} {_fmt_coord(new_rot)})",
        block,
        count=1,
    )
    if token == "global_label":
        # Update Intersheetrefs (at ...) — typically rot 0.
        new_block = re.sub(
            r'(\(property\s+"Intersheetrefs"[^\n]*\n\s*)\(at\s+-?[\d.]+\s+-?[\d.]+(?:\s+-?[\d.]+)?\s*\)',
            lambda m: f"{m.group(1)}(at {_fmt_coord(new_x)} {_fmt_coord(new_y)} {_fmt_coord(new_rot)})",
            new_block,
            count=1,
        )

    new_text = text[:open_idx] + new_block + text[end_idx:]
    changed = _maybe_write(sch_path, text, new_text, dry_run)
    return {
        "action": "move_label",
        "changed": changed,
        "diff": _diff(text, new_text, sch_path),
        "details": {
            "uuid": uuid,
            "kind": token,
            "old": {"x": old_x, "y": old_y, "rotation": old_rot},
            "new": {"x": new_x, "y": new_y, "rotation": new_rot},
        },
    }


def delete_label(
    sch_path: str | Path,
    uuid: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    sch_path = Path(sch_path)
    text = _read(sch_path)
    found = _find_label_block_by_uuid(text, uuid)
    if not found:
        raise ValueError(f"label with uuid {uuid!r} not found")
    token, open_idx, end_idx, _block = found
    start, stop = _expand_block_with_indent(text, open_idx, end_idx)
    new_text = text[:start] + text[stop:]
    changed = _maybe_write(sch_path, text, new_text, dry_run)
    return {
        "action": "delete_label",
        "changed": changed,
        "diff": _diff(text, new_text, sch_path),
        "details": {"uuid": uuid, "kind": token},
    }


# ---------------------------------------------------------------------------
# Junctions
# ---------------------------------------------------------------------------


def _junction_block(x: float, y: float, uuid_str: str) -> str:
    return (
        f"\t(junction\n"
        f"\t\t(at {_fmt_coord(x)} {_fmt_coord(y)})\n"
        f"\t\t(diameter 0)\n"
        f"\t\t(color 0 0 0 0)\n"
        f'\t\t(uuid "{uuid_str}")\n'
        f"\t)\n"
    )


def add_junction(
    sch_path: str | Path,
    at: tuple[float, float],
    dry_run: bool = False,
) -> dict[str, Any]:
    sch_path = Path(sch_path)
    text = _read(sch_path)
    uuid_str = _new_uuid()
    block = _junction_block(at[0], at[1], uuid_str)

    last_end: int | None = None
    for open_idx, end_idx in _iter_top_blocks(text, "junction"):
        last_end = end_idx
    if last_end is not None:
        insert_at = last_end
        if insert_at < len(text) and text[insert_at] == "\n":
            insert_at += 1
        new_text = text[:insert_at] + block + text[insert_at:]
    else:
        last_paren = text.rstrip().rfind(")")
        if last_paren < 0:
            raise ValueError("could not locate end of schematic")
        new_text = text[:last_paren] + block + text[last_paren:]

    changed = _maybe_write(sch_path, text, new_text, dry_run)
    return {
        "action": "add_junction",
        "changed": changed,
        "diff": _diff(text, new_text, sch_path),
        "details": {"uuid": uuid_str, "at": [float(at[0]), float(at[1])]},
    }


def delete_junction(
    sch_path: str | Path,
    uuid: str | None = None,
    at: tuple[float, float] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    sch_path = Path(sch_path)
    text = _read(sch_path)
    if uuid is None and at is None:
        raise ValueError("delete_junction requires uuid or at")

    target: tuple[int, int] | None = None
    matched_uuid: str | None = None
    matched_at: tuple[float, float, float] | None = None
    for open_idx, end_idx in _iter_top_blocks(text, "junction"):
        block = text[open_idx:end_idx]
        juuid = _block_uuid(block)
        jat = _block_at(block)
        if uuid is not None and juuid == uuid:
            target = (open_idx, end_idx)
            matched_uuid = juuid
            matched_at = jat
            break
        if uuid is None and at is not None and jat is not None:
            if _coords_close(jat[0], at[0]) and _coords_close(jat[1], at[1]):
                target = (open_idx, end_idx)
                matched_uuid = juuid
                matched_at = jat
                break

    if target is None:
        raise ValueError("no matching junction found")
    start, stop = _expand_block_with_indent(text, target[0], target[1])
    new_text = text[:start] + text[stop:]
    changed = _maybe_write(sch_path, text, new_text, dry_run)
    return {
        "action": "delete_junction",
        "changed": changed,
        "diff": _diff(text, new_text, sch_path),
        "details": {
            "uuid": matched_uuid,
            "at": list(matched_at[:2]) if matched_at else None,
        },
    }


__all__ = [
    "move_symbol",
    "move_symbol_property",
    "add_wire",
    "delete_wire",
    "add_label",
    "move_label",
    "delete_label",
    "add_junction",
    "delete_junction",
    "add_pin",
]


# ---------------------------------------------------------------------------
# add_pin (symbol pin in lib_symbols + standalone .kicad_sym)
# ---------------------------------------------------------------------------


_PIN_ELECTRICAL_TYPES = {
    "input",
    "output",
    "bidirectional",
    "tri_state",
    "passive",
    "free",
    "unspecified",
    "power_in",
    "power_out",
    "open_collector",
    "open_emitter",
    "no_connect",
}

_PIN_SHAPES = {
    "line",
    "inverted",
    "clock",
    "inverted_clock",
    "input_low",
    "clock_low",
    "output_low",
    "edge_clock_high",
    "non_logic",
}


def _fmt_coord_2(v: float) -> str:
    """Round to 2 decimals then drop trailing zeros (KiCad style)."""
    return _fmt_coord(round(float(v), 2))


def _build_pin_block(
    base_indent: str,
    indent_unit: str,
    electrical_type: str,
    shape: str,
    x: float,
    y: float,
    rot: float,
    length: float,
    name: str,
    number: str,
    font_size: float,
) -> str:
    """Render a (pin ...) block with the given base indent (the indent of the pin keyword line).

    Returns text with a trailing newline.
    """
    i0 = base_indent
    i1 = base_indent + indent_unit
    i2 = base_indent + indent_unit * 2
    i3 = base_indent + indent_unit * 3
    i4 = base_indent + indent_unit * 4
    fs = _fmt_coord(font_size)
    lines = [
        f"{i0}(pin {electrical_type} {shape}",
        f"{i1}(at {_fmt_coord_2(x)} {_fmt_coord_2(y)} {_fmt_coord_2(rot)})",
        f"{i1}(length {_fmt_coord_2(length)})",
        f'{i1}(name "{name}"',
        f"{i2}(effects",
        f"{i3}(font",
        f"{i4}(size {fs} {fs})",
        f"{i3})",
        f"{i2})",
        f"{i1})",
        f'{i1}(number "{number}"',
        f"{i2}(effects",
        f"{i3}(font",
        f"{i4}(size {fs} {fs})",
        f"{i3})",
        f"{i2})",
        f"{i1})",
        f"{i0})",
        "",
    ]
    return "\n".join(lines)


def _find_inner_block(text: str, outer_open: int, outer_end: int, child_token: str):
    """Yield (open_idx, end_idx) for direct children `(child_token ...)` inside outer block.

    Direct children are detected by paren-depth == 1 relative to outer_open.
    """
    i = outer_open + 1
    n = outer_end
    in_str = False
    depth = 1  # we're inside outer's '('
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            i += 1
            continue
        if c == "(":
            if depth == 1:
                # check token name
                j = i + 1
                while j < n and text[j].isspace():
                    j += 1
                k = j
                while k < n and (text[k].isalnum() or text[k] == "_"):
                    k += 1
                tok = text[j:k]
                if tok == child_token:
                    end = _find_block_end(text, i)
                    yield i, end
                    i = end
                    continue
            depth += 1
            i += 1
            continue
        if c == ")":
            depth -= 1
            if depth == 0:
                return
            i += 1
            continue
        i += 1


def _find_named_symbol_block(text: str, search_open: int, search_end: int, name: str):
    """Find a direct-child (symbol "<name>" ...) block within [search_open, search_end].

    Returns (open_idx, end_idx) or None.
    """
    for open_idx, end_idx in _find_inner_block(text, search_open, search_end, "symbol"):
        # extract first quoted string after `(symbol`
        head = text[open_idx : min(open_idx + 200, end_idx)]
        m = re.match(r'\(symbol\s+"([^"]+)"', head)
        if m and m.group(1) == name:
            return open_idx, end_idx
    return None


def _find_unit_with_pins(text: str, outer_open: int, outer_end: int):
    """Among direct-child (symbol ...) blocks under outer, return the first that contains
    a (pin ...) child. If none, return the first child symbol block. Returns
    (open_idx, end_idx) or None.
    """
    first = None
    for sopen, send in _find_inner_block(text, outer_open, outer_end, "symbol"):
        if first is None:
            first = (sopen, send)
        for _ in _find_inner_block(text, sopen, send, "pin"):
            return sopen, send
    return first


def _pin_number(block: str) -> str | None:
    m = re.search(r'\(number\s+"([^"]+)"', block)
    return m.group(1) if m else None


def _existing_pin_numbers(text: str, unit_open: int, unit_end: int) -> set[str]:
    nums: set[str] = set()
    for popen, pend in _find_inner_block(text, unit_open, unit_end, "pin"):
        n = _pin_number(text[popen:pend])
        if n is not None:
            nums.add(n)
    return nums


def _line_indent(text: str, idx: int) -> str:
    """Return the whitespace prefix of the line containing idx."""
    line_start = text.rfind("\n", 0, idx) + 1
    j = line_start
    while j < len(text) and text[j] in (" ", "\t"):
        j += 1
    return text[line_start:j]


def _insert_pin_into_unit(
    text: str,
    unit_open: int,
    unit_end: int,
    electrical_type: str,
    shape: str,
    x: float,
    y: float,
    rot: float,
    length: float,
    name: str,
    number: str,
    font_size: float,
) -> str:
    """Insert a new pin block as the last child pin of the unit symbol block."""
    indent_unit = _detect_indent_unit(text)
    # Determine pin indent: prefer indent of an existing pin in this unit; else unit indent + 1.
    last_pin: tuple[int, int] | None = None
    for popen, pend in _find_inner_block(text, unit_open, unit_end, "pin"):
        last_pin = (popen, pend)
    if last_pin is not None:
        base_indent = _line_indent(text, last_pin[0])
        insert_at = last_pin[1]
        # consume trailing newline after the pin block
        if insert_at < len(text) and text[insert_at] == "\n":
            insert_at += 1
        block = _build_pin_block(
            base_indent, indent_unit, electrical_type, shape, x, y, rot, length, name, number, font_size
        )
        return text[:insert_at] + block + text[insert_at:]
    # No existing pin: insert just before the closing paren of the unit symbol.
    unit_indent = _line_indent(text, unit_open)
    base_indent = unit_indent + indent_unit
    # find the line containing the closing paren
    close_idx = unit_end - 1  # index of ')'
    line_start = text.rfind("\n", 0, close_idx) + 1
    block = _build_pin_block(
        base_indent, indent_unit, electrical_type, shape, x, y, rot, length, name, number, font_size
    )
    return text[:line_start] + block + text[line_start:]


def _find_lib_symbols_block(text: str) -> tuple[int, int] | None:
    """Find the (lib_symbols ...) block. In schematics it's at depth 1 (tab-indented).
    In .kicad_sym files this is not used (top-level is kicad_symbol_lib)."""
    pat = re.compile(r"^\t\(lib_symbols\b", re.MULTILINE)
    m = pat.search(text)
    if not m:
        return None
    open_idx = m.start() + 1
    return open_idx, _find_block_end(text, open_idx)


def add_pin(
    sch_path: str | Path,
    lib_id: str,
    number: str,
    name: str,
    at: tuple[float, float, float],
    length: float,
    electrical_type: str,
    shape: str = "line",
    lib_file: str | Path | None = None,
    font_size: float = 1.27,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Add a pin to a symbol in the schematic's embedded lib_symbols and (if it
    matches the namespace) the standalone .kicad_sym library file.

    Args:
        lib_id: e.g. "project:PLACEHOLDER_1" — outer library symbol id.
        at: (x, y, rotation) in symbol-local coordinates.
    """
    if electrical_type not in _PIN_ELECTRICAL_TYPES:
        raise ValueError(
            f"--type must be one of {sorted(_PIN_ELECTRICAL_TYPES)}, got {electrical_type!r}"
        )
    if shape not in _PIN_SHAPES:
        raise ValueError(f"--shape must be one of {sorted(_PIN_SHAPES)}, got {shape!r}")

    sch_path = Path(sch_path)
    text = _read(sch_path)

    if ":" in lib_id:
        ns, sym_name = lib_id.split(":", 1)
    else:
        ns, sym_name = "", lib_id

    x, y, rot = float(at[0]), float(at[1]), float(at[2]) if len(at) > 2 else 0.0
    length_f = float(length)

    # ---- schematic update ----
    libsyms = _find_lib_symbols_block(text)
    if libsyms is None:
        raise ValueError(f"(lib_symbols ...) block not found in {sch_path}")
    ls_open, ls_end = libsyms

    outer = _find_named_symbol_block(text, ls_open, ls_end, lib_id)
    if outer is None:
        raise ValueError(
            f'symbol "{lib_id}" not found in (lib_symbols ...) of {sch_path}'
        )
    outer_open, outer_end = outer

    unit = _find_unit_with_pins(text, outer_open, outer_end)
    if unit is None:
        raise ValueError(f'no inner unit symbol found inside "{lib_id}"')
    unit_open, unit_end = unit

    existing = _existing_pin_numbers(text, unit_open, unit_end)
    if number in existing:
        raise ValueError(
            f'pin number "{number}" already exists in "{lib_id}" (schematic embedded lib)'
        )

    new_text = _insert_pin_into_unit(
        text,
        unit_open,
        unit_end,
        electrical_type,
        shape,
        x,
        y,
        rot,
        length_f,
        name,
        number,
        font_size,
    )
    sch_changed = _maybe_write(sch_path, text, new_text, dry_run)
    sch_diff = _diff(text, new_text, sch_path)

    # ---- lib file update ----
    lib_info: dict[str, Any] = {"changed": False, "path": None, "diff_summary": None}
    if lib_file is None:
        lib_info["skipped"] = "no standalone lib file requested"
        combined_diff = sch_diff
        return {
            "action": "add_pin",
            "changed": sch_changed,
            "diff": combined_diff,
            "details": {
                "schematic": {
                    "path": str(sch_path),
                    "changed": sch_changed,
                    "diff_summary": sch_diff,
                },
                "lib_file": lib_info,
                "pin": {
                    "number": number,
                    "name": name,
                    "at": [round(x, 2), round(y, 2), round(rot, 2)],
                    "length": round(length_f, 2),
                    "type": electrical_type,
                    "shape": shape,
                },
            },
        }
    lib_path = Path(lib_file)
    lib_info["path"] = str(lib_path)

    # Auto-skip if namespace doesn't match the lib file's stem.
    expected_ns = lib_path.stem
    if ns and ns != expected_ns:
        lib_info["skipped"] = (
            f"lib-id namespace {ns!r} does not match lib file stem {expected_ns!r}"
        )
    elif not lib_path.exists():
        lib_info["skipped"] = f"lib file does not exist: {lib_path}"
    else:
        lib_text = _read(lib_path)
        # Top-level (kicad_symbol_lib ...) — find (symbol "<sym_name>" ...) at depth 1.
        # Use _iter_top_blocks for "symbol" but depth-1 indent here is also tab.
        target = None
        for open_idx, end_idx in _iter_top_blocks(lib_text, "symbol"):
            head = lib_text[open_idx : min(open_idx + 200, end_idx)]
            m = re.match(r'\(symbol\s+"([^"]+)"', head)
            if m and m.group(1) == sym_name:
                target = (open_idx, end_idx)
                break
        if target is None:
            lib_info["skipped"] = (
                f'no (symbol "{sym_name}" ...) found in {lib_path}'
            )
        else:
            l_outer_open, l_outer_end = target
            l_unit = _find_unit_with_pins(lib_text, l_outer_open, l_outer_end)
            if l_unit is None:
                lib_info["skipped"] = (
                    f'no inner unit symbol inside "{sym_name}" in {lib_path}'
                )
            else:
                l_unit_open, l_unit_end = l_unit
                l_existing = _existing_pin_numbers(lib_text, l_unit_open, l_unit_end)
                if number in l_existing:
                    raise ValueError(
                        f'pin number "{number}" already exists in "{sym_name}" '
                        f"({lib_path})"
                    )
                new_lib_text = _insert_pin_into_unit(
                    lib_text,
                    l_unit_open,
                    l_unit_end,
                    electrical_type,
                    shape,
                    x,
                    y,
                    rot,
                    length_f,
                    name,
                    number,
                    font_size,
                )
                lib_changed = _maybe_write(lib_path, lib_text, new_lib_text, dry_run)
                lib_info["changed"] = lib_changed
                lib_info["diff_summary"] = _diff(lib_text, new_lib_text, lib_path)

    # Combined diff is reported for stdout (sch first, then lib if any).
    combined_diff = sch_diff
    if lib_info.get("diff_summary"):
        combined_diff = sch_diff + lib_info["diff_summary"]

    return {
        "action": "add_pin",
        "changed": sch_changed or bool(lib_info.get("changed")),
        "diff": combined_diff,
        "details": {
            "schematic": {
                "path": str(sch_path),
                "changed": sch_changed,
                "diff_summary": sch_diff,
            },
            "lib_file": lib_info,
            "pin": {
                "number": number,
                "name": name,
                "at": [round(x, 2), round(y, 2), round(rot, 2)],
                "length": round(length_f, 2),
                "type": electrical_type,
                "shape": shape,
            },
        },
    }
