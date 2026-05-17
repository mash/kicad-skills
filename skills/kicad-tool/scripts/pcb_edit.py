"""Pure S-expression text mutation for KiCad 10 PCB (.kicad_pcb) files.

Mirrors the design of ``sch_edit.py``: locate a top-level ``(footprint ...)``
block by its Reference property, patch only the minimal fields with raw text
substitutions, preserve formatting and UUIDs, and emit a unified diff.

We deliberately do NOT round-trip ``.kicad_pcb`` through kiutils for writes —
formatting and UUIDs must be preserved byte-for-byte outside the mutated
region. kiutils may be used read-only to look up state if needed.

Supported actions:

- ``move_footprint``: change the footprint's own ``(at X Y [R])`` (and only
  that block changes).
- ``move_footprint_property``: change a single property's ``(at X Y [R])``
  inside the footprint block (e.g. Reference / Value).
- ``move_footprint_layer``: explicit board-side flip. Maps front|back to
  ``F.Cu`` / ``B.Cu``, swaps every ``F.<name>`` <-> ``B.<name>`` layer
  reference inside the footprint, mirrors local Y coordinates of geometry,
  and (optionally) updates the footprint position / rotation. Refuses to
  no-op-flip (target side equals current side); use ``move`` instead.

Locked footprints are refused for every mutating action.
"""

from __future__ import annotations

import difflib
import os
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


# ---------------------------------------------------------------------------
# Generic helpers (kept independent of sch_edit so this module stands alone)
# ---------------------------------------------------------------------------


def _fmt_coord(v: float) -> str:
    """Format a coordinate the way KiCad emits PCB coords."""
    if isinstance(v, int):
        if v == 0:
            return "0"
        return str(v)
    # Normalize -0.0 -> 0.0 to avoid emitting "-0".
    if v == 0:
        v = 0.0
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    if s in ("", "-0"):
        return "0"
    return s


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
    if old == new:
        return False
    if not dry_run:
        Path(path).write_text(new, encoding="utf-8")
    return True


def _find_block_end(text: str, open_idx: int) -> int:
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


def _iter_top_blocks(text: str, token: str):
    """Yield (open_idx, end_idx) for each ``(token ...)`` block at depth 1
    (tab indent). Matches the formatting kicad-cli / pcbnew emit."""
    pat = re.compile(r"^\t\(" + re.escape(token) + r"\b", re.MULTILINE)
    for m in pat.finditer(text):
        open_idx = m.start() + 1  # skip leading tab
        end_idx = _find_block_end(text, open_idx)
        yield open_idx, end_idx


# ---------------------------------------------------------------------------
# Footprint locator
# ---------------------------------------------------------------------------


def _find_footprint_block_by_ref(text: str, ref: str) -> tuple[int, int, str] | None:
    """Locate a top-level ``(footprint ...)`` block whose Reference property
    equals ``ref``.

    Returns (open_idx, end_idx, block_text) or None.
    """
    for open_idx, end_idx in _iter_top_blocks(text, "footprint"):
        block = text[open_idx:end_idx]
        m = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', block)
        if m and m.group(1) == ref:
            return open_idx, end_idx, block
    return None


def _is_locked(block: str) -> bool:
    """Detect a locked footprint.

    KiCad encodes lock state as either:
      * a bare ``locked`` token after the lib id on the footprint head, e.g.
        ``(footprint "R_0603" locked (layer "F.Cu") ...)``
      * an ``(attr ... locked ...)`` form inside the block.
    """
    head_match = re.match(r'\(footprint\s+"[^"]*"\s+([a-z_]+)\b', block)
    if head_match and head_match.group(1) == "locked":
        return True
    if re.search(r"\(attr\b[^)]*\blocked\b", block):
        return True
    return False


def _block_layer(block: str) -> str | None:
    """Extract the footprint's own (layer "...") subform.

    Anchored to the footprint head: walks top-level subforms and returns the
    first ``(layer "..." )`` (or ``(layer X)``) child. Pads/drawings/properties
    have their own (layer ...) nested deeper, which we must NOT pick up here.
    """
    body_start = _footprint_head_end(block)
    for head, open_idx, end_idx in _iter_subforms(block, body_start):
        if head == "layer":
            inner = block[open_idx:end_idx]
            m = re.match(r'\(layer\s+"([^"]+)"\s*\)', inner)
            if m:
                return m.group(1)
            m = re.match(r'\(layer\s+([A-Za-z0-9_.]+)\s*\)', inner)
            if m:
                return m.group(1)
    return None


_AT_RE = re.compile(r"\(at\s+(-?[\d.]+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?\s*\)")


def _footprint_head_end(block: str) -> int:
    """Return the offset just past the ``(footprint "LIB:Name" [locked])`` head.

    The next character is where the body subforms start (newline or space).
    """
    m = re.match(r'\(footprint\s+"[^"]*"(?:\s+[a-z_]+)?', block)
    if not m:
        raise ValueError("block does not start with (footprint \"...\"")
    return m.end()


def _iter_subforms(block: str, body_start: int):
    """Yield (head_token, open_idx, end_idx) for each top-level subform of the
    footprint block (i.e. children of the (footprint ...) form), starting at
    ``body_start`` and stopping at the block's closing ')'.
    """
    n = len(block)
    i = body_start
    while i < n:
        c = block[i]
        if c == ")":
            # Close of footprint form.
            return
        if c != "(":
            i += 1
            continue
        end_idx = _find_block_end(block, i)
        m = re.match(r"\(([A-Za-z_][A-Za-z0-9_]*)", block[i:end_idx])
        head = m.group(1) if m else ""
        yield head, i, end_idx
        i = end_idx


def _find_first_top_at(block: str) -> re.Match | None:
    """Locate the FIRST top-level ``(at ...)`` subform of the footprint block.

    Anchored to the footprint head (does not match (at) nested in properties,
    pads, drawings, etc.). Returns a re.Match positioned at the absolute offset
    in ``block``, or None.
    """
    body_start = _footprint_head_end(block)
    for head, open_idx, end_idx in _iter_subforms(block, body_start):
        if head == "at":
            text = block[open_idx:end_idx]
            m = _AT_RE.match(text)
            if m:
                # Re-run search from the same offset so the returned Match's
                # span() is in absolute block coordinates.
                return _AT_RE.search(block, open_idx, end_idx)
    return None


def _block_at(block: str) -> tuple[float, float, float] | None:
    m = _find_first_top_at(block)
    if not m:
        return None
    x, y = float(m.group(1)), float(m.group(2))
    rot = float(m.group(3)) if m.group(3) is not None else 0.0
    return x, y, rot


# ---------------------------------------------------------------------------
# move_footprint
# ---------------------------------------------------------------------------


def move_footprint(
    pcb_path: str | Path,
    ref: str,
    to: tuple[float, float],
    rotation: float | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Move a footprint by reference.

    Only the footprint's own ``(at X Y [R])`` is mutated. Pads, drawings, and
    properties move with the footprint implicitly because their coordinates
    are local to the footprint origin.
    """
    pcb_path = Path(pcb_path)
    text = _read(pcb_path)

    found = _find_footprint_block_by_ref(text, ref)
    if not found:
        raise ValueError(f"footprint with reference {ref!r} not found in {pcb_path}")
    open_idx, end_idx, block = found

    if _is_locked(block):
        raise ValueError(f"footprint {ref!r} is locked; refusing to move")

    cur_at = _block_at(block)
    if cur_at is None:
        raise ValueError(f"footprint {ref!r} has no (at ...) field")
    old_x, old_y, old_rot = cur_at
    new_x, new_y = float(to[0]), float(to[1])
    new_rot = float(rotation) if rotation is not None else old_rot

    # Replace ONLY the first top-level (at ...) inside the block — that is the
    # footprint's own position. Anchored to the footprint head so we cannot
    # accidentally pick an (at) nested in a property/pad/drawing.
    m_at = _find_first_top_at(block)
    if not m_at:
        raise ValueError("footprint (at ...) not found")
    if rotation is None and old_rot == 0 and len(m_at.group(0).split()) == 3:
        # Preserve "(at x y)" form when no rotation present and unchanged.
        new_at = f"(at {_fmt_coord(new_x)} {_fmt_coord(new_y)})"
    else:
        new_at = f"(at {_fmt_coord(new_x)} {_fmt_coord(new_y)} {_fmt_coord(new_rot)})"
    new_block = block[: m_at.start()] + new_at + block[m_at.end() :]

    new_text = text[:open_idx] + new_block + text[end_idx:]

    changed = _maybe_write(pcb_path, text, new_text, dry_run)
    return {
        "action": "move_footprint",
        "changed": changed,
        "diff": _diff(text, new_text, pcb_path),
        "details": {
            "ref": ref,
            "old": {"x": old_x, "y": old_y, "rotation": old_rot},
            "new": {"x": new_x, "y": new_y, "rotation": new_rot},
            "delta": {
                "dx": new_x - old_x,
                "dy": new_y - old_y,
                "drot": new_rot - old_rot,
            },
        },
    }


# ---------------------------------------------------------------------------
# move_footprint_property
# ---------------------------------------------------------------------------


def move_footprint_property(
    pcb_path: str | Path,
    ref: str,
    key: str,
    to: tuple[float, float],
    rotation: float | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Move a single property's placement inside a footprint block.

    ``key`` is matched literally (e.g. ``Reference``, ``Value``, ``Datasheet``,
    ``Footprint``, ``Description``, or any user-defined property the board
    actually contains).
    """
    pcb_path = Path(pcb_path)
    text = _read(pcb_path)

    found = _find_footprint_block_by_ref(text, ref)
    if not found:
        raise ValueError(f"footprint with reference {ref!r} not found in {pcb_path}")
    fp_open_idx, fp_end_idx, fp_block = found

    if _is_locked(fp_block):
        raise ValueError(f"footprint {ref!r} is locked; refusing to move property")

    prop_head_pat = re.compile(r'\(property\s+"' + re.escape(key) + r'"\s+"[^"]*"')
    m_head = prop_head_pat.search(fp_block)
    if not m_head:
        raise ValueError(f"property {key!r} not found on footprint {ref!r}")

    prop_open_rel = m_head.start()
    prop_end_rel = _find_block_end(fp_block, prop_open_rel)
    prop_block = fp_block[prop_open_rel:prop_end_rel]

    at_pat = re.compile(r"\(at\s+(-?[\d.]+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?\s*\)")
    m_at = at_pat.search(prop_block)
    if not m_at:
        raise ValueError(f"property {key!r} on footprint {ref!r} has no (at ...) placement")

    old_x, old_y = float(m_at.group(1)), float(m_at.group(2))
    old_rot_raw = m_at.group(3)
    had_rot = old_rot_raw is not None
    old_rot = float(old_rot_raw) if old_rot_raw is not None else 0.0

    new_x, new_y = float(to[0]), float(to[1])
    new_rot = float(rotation) if rotation is not None else old_rot

    if rotation is None and not had_rot:
        new_at = f"(at {_fmt_coord(new_x)} {_fmt_coord(new_y)})"
    else:
        new_at = f"(at {_fmt_coord(new_x)} {_fmt_coord(new_y)} {_fmt_coord(new_rot)})"

    new_prop_block = at_pat.sub(new_at, prop_block, count=1)
    new_fp_block = fp_block[:prop_open_rel] + new_prop_block + fp_block[prop_end_rel:]
    new_text = text[:fp_open_idx] + new_fp_block + text[fp_end_idx:]

    changed = _maybe_write(pcb_path, text, new_text, dry_run)
    return {
        "action": "move_footprint_property",
        "changed": changed,
        "diff": _diff(text, new_text, pcb_path),
        "details": {
            "ref": ref,
            "key": key,
            "old": {"x": old_x, "y": old_y, "rotation": old_rot, "had_rotation": had_rot},
            "new": {
                "x": new_x,
                "y": new_y,
                "rotation": new_rot,
                "had_rotation": rotation is not None or had_rot,
            },
        },
    }


# ---------------------------------------------------------------------------
# set_footprint_property
# ---------------------------------------------------------------------------


def set_footprint_property(
    pcb_path: str | Path,
    ref: str,
    key: str,
    value: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Set a single property's string value inside a footprint block.

    ``key`` is matched literally (e.g. ``Value``, ``Description``, ``MPN``,
    ``LCSC``). The property must already exist on the footprint; if it does
    not, a ``ValueError`` is raised. Only the quoted value is rewritten — any
    sub-forms (``(at ...)``, ``(layer ...)``, ``(uuid ...)``, ``(effects ...)``)
    are preserved verbatim. Only the first matching occurrence inside the
    footprint block is changed.
    """
    pcb_path = Path(pcb_path)
    if key == "Reference":
        raise KeyError(
            "set Reference via the schematic + 'pcb sync' instead "
            "of editing the board directly"
        )
    text = _read(pcb_path)

    found = _find_footprint_block_by_ref(text, ref)
    if not found:
        raise ValueError(f"footprint with reference {ref!r} not found in {pcb_path}")
    fp_open_idx, fp_end_idx, fp_block = found

    if _is_locked(fp_block):
        raise ValueError(f"footprint {ref!r} is locked; refusing to set property")

    prop_pat = re.compile(
        r'(\(property\s+"' + re.escape(key) + r'"\s+")([^"]*)(")'
    )
    m = prop_pat.search(fp_block)
    if not m:
        raise ValueError(f"property {key!r} not found on footprint {ref!r}")

    old_val = m.group(2)
    new_prop_head = m.group(1) + value + m.group(3)
    new_fp_block = fp_block[: m.start()] + new_prop_head + fp_block[m.end():]
    new_text = text[:fp_open_idx] + new_fp_block + text[fp_end_idx:]

    changed = _maybe_write(pcb_path, text, new_text, dry_run)
    return {
        "action": "set_footprint_property",
        "changed": changed,
        "diff": _diff(text, new_text, pcb_path),
        "details": {
            "ref": ref,
            "key": key,
            "old": old_val,
            "new": value,
        },
    }


# ---------------------------------------------------------------------------
# move_footprint_layer (flip)
# ---------------------------------------------------------------------------


# Side-specific layer pairs that swap when a footprint flips. KiCad emits the
# canonical short names for these (F.Cu / B.Cu, F.SilkS / B.SilkS, ...). We
# also handle a couple of long-form aliases (e.g. F.Adhesive) defensively in
# case a board file uses them.
_LAYER_FLIP_PAIRS: tuple[tuple[str, str], ...] = (
    ("F.Cu", "B.Cu"),
    ("F.SilkS", "B.SilkS"),
    ("F.Silkscreen", "B.Silkscreen"),
    ("F.Fab", "B.Fab"),
    ("F.Paste", "B.Paste"),
    ("F.Mask", "B.Mask"),
    ("F.CrtYd", "B.CrtYd"),
    ("F.Courtyard", "B.Courtyard"),
    ("F.Adhes", "B.Adhes"),
    ("F.Adhesive", "B.Adhesive"),
)


def _flip_layer_name(name: str) -> str:
    for f, b in _LAYER_FLIP_PAIRS:
        if name == f:
            return b
        if name == b:
            return f
    return name


def _flip_layer_strings_in_block(block: str) -> str:
    """Swap F.<x>/B.<x> tokens only inside (layer ...) and (layers ...)
    S-expressions. Other text (string literals, property values, library
    names) is left untouched.
    """

    token_pat = re.compile(r'"([A-Za-z0-9_.]+)"|([A-Za-z0-9_.]+)')

    def _swap_tokens(inner: str) -> str:
        def _sub(m: re.Match) -> str:
            quoted, bare = m.group(1), m.group(2)
            if quoted is not None:
                return '"' + _flip_layer_name(quoted) + '"'
            return _flip_layer_name(bare)

        return token_pat.sub(_sub, inner)

    # Walk the block, using a depth tracker to extract each (layer ...) and
    # (layers ...) form (including any nested subforms in the body) and swap
    # F.* <-> B.* tokens only inside those forms. This is robust against any
    # future KiCad output that might nest parens inside a (layers ...) body.
    head_pat = re.compile(r'\((layer|layers)\b')
    out: list[str] = []
    cursor = 0
    for m in head_pat.finditer(block):
        open_idx = m.start()
        if open_idx < cursor:
            # Already consumed as part of an outer (layer/layers ...) form.
            continue
        end_idx = _find_block_end(block, open_idx)
        head = m.group(1)
        # Body is everything between "(layer" / "(layers" and the closing ')'.
        head_end = open_idx + 1 + len(head)  # past "(layer" or "(layers"
        body = block[head_end : end_idx - 1]
        new_body = _swap_tokens(body)
        out.append(block[cursor:open_idx])
        out.append(f"({head}{new_body})")
        cursor = end_idx
    out.append(block[cursor:])
    return "".join(out)


# Top-level subforms of (footprint ...) whose internal coordinates are
# geometric and must be mirrored on flip. Property and fp_text placement is
# left alone — KiCad rotates those itself when the side flips, and forcing
# Y-negation here corrupts user-positioned text on a single flip.
_GEOMETRY_HEADS: frozenset[str] = frozenset(
    {"fp_line", "fp_arc", "fp_circle", "fp_poly", "fp_rect", "pad"}
)


def _negate_y_in_geometry(text: str, head: str = "") -> str:
    """Mirror Y across X-axis inside one geometry subform's text.

    Negates Y in (at X Y [R]), (xy X Y), (start X Y), (end X Y),
    (center X Y), (mid X Y).

    For ``pad`` subforms specifically, pad rotation R in ``(at X Y R)`` is
    also negated (KiCad's pad-flip semantics: R' = -R). For other geometry
    forms (fp_line/fp_arc/fp_circle/fp_poly/fp_rect), any third numeric in
    ``(at ...)`` is left untouched.
    """
    at_pat = re.compile(r"\(at\s+(-?[\d.]+)\s+(-?[\d.]+)((?:\s+-?[\d.]+)?)\s*\)")

    is_pad = head == "pad"

    def _sub_at(m: re.Match) -> str:
        x = float(m.group(1))
        y = -float(m.group(2))
        rest = m.group(3).strip()
        if rest:
            if is_pad:
                # Negate the pad rotation alongside Y mirroring.
                rot = -float(rest)
                return f"(at {_fmt_coord(x)} {_fmt_coord(y)} {_fmt_coord(rot)})"
            return f"(at {_fmt_coord(x)} {_fmt_coord(y)} {rest})"
        return f"(at {_fmt_coord(x)} {_fmt_coord(y)})"

    text = at_pat.sub(_sub_at, text)

    xy_pat = re.compile(r"\(xy\s+(-?[\d.]+)\s+(-?[\d.]+)\s*\)")

    def _sub_xy(m: re.Match) -> str:
        x = float(m.group(1))
        y = -float(m.group(2))
        return f"(xy {_fmt_coord(x)} {_fmt_coord(y)})"

    text = xy_pat.sub(_sub_xy, text)

    se_pat = re.compile(r"\((start|end|center|mid)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*\)")

    def _sub_se(m: re.Match) -> str:
        tok = m.group(1)
        x = float(m.group(2))
        y = -float(m.group(3))
        return f"({tok} {_fmt_coord(x)} {_fmt_coord(y)})"

    return se_pat.sub(_sub_se, text)


def _mirror_geometry_subforms(block: str) -> str:
    """Walk top-level subforms of the footprint and Y-mirror geometry forms
    only. Property/fp_text/footprint-origin (at) are left alone.
    """
    body_start = _footprint_head_end(block)
    spans = list(_iter_subforms(block, body_start))
    out: list[str] = [block[:body_start]]
    cursor = body_start
    for head, open_idx, end_idx in spans:
        out.append(block[cursor:open_idx])
        sub = block[open_idx:end_idx]
        if head in _GEOMETRY_HEADS:
            sub = _negate_y_in_geometry(sub, head)
        out.append(sub)
        cursor = end_idx
    out.append(block[cursor:])
    return "".join(out)


_SIDE_TO_CU = {"front": "F.Cu", "back": "B.Cu"}


def move_footprint_layer(
    pcb_path: str | Path,
    ref: str,
    side: str,
    at: tuple[float, float] | None = None,
    rotation: float | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Flip a footprint to the given board side.

    ``side`` is ``"front"`` or ``"back"`` and is internally mapped to
    ``F.Cu`` / ``B.Cu``. The operation:

    1. Swaps the footprint's own ``(layer "F.Cu")`` <-> ``(layer "B.Cu")``.
    2. Swaps every ``F.<name>`` <-> ``B.<name>`` inside the block (silk, fab,
       paste, mask, courtyard, adhesive).
    3. Mirrors local Y of every ``(at X Y [R])`` (except the footprint's own
       origin), ``(xy ...)``, and ``(start/end/center/mid ...)`` form. This
       matches KiCad's flip-around-X semantics for footprints.
    4. Optionally updates the footprint's own ``(at X Y R)`` if ``at`` /
       ``rotation`` are supplied; otherwise the origin and rotation are
       preserved as-is.

    Round-tripping front -> back -> front returns the footprint to its
    original geometry (modulo coordinate-format normalization).
    """
    if side not in _SIDE_TO_CU:
        raise ValueError(f"side must be 'front' or 'back', got {side!r}")
    target_layer = _SIDE_TO_CU[side]

    pcb_path = Path(pcb_path)
    text = _read(pcb_path)

    found = _find_footprint_block_by_ref(text, ref)
    if not found:
        raise ValueError(f"footprint with reference {ref!r} not found in {pcb_path}")
    open_idx, end_idx, block = found

    if _is_locked(block):
        raise ValueError(f"footprint {ref!r} is locked; refusing to flip")

    cur_layer = _block_layer(block)
    if cur_layer is None:
        raise ValueError(f"footprint {ref!r} has no (layer ...) field")
    if cur_layer == target_layer:
        raise ValueError(
            f"footprint {ref!r} already on layer {cur_layer!r}; "
            "use 'move' instead of 'move-layer'"
        )

    cur_at = _block_at(block)
    if cur_at is None:
        raise ValueError(f"footprint {ref!r} has no (at ...) field")
    old_x, old_y, old_rot = cur_at
    new_x = float(at[0]) if at is not None else old_x
    new_y = float(at[1]) if at is not None else old_y
    new_rot = float(rotation) if rotation is not None else old_rot

    # Step 1+2: swap F.* <-> B.* layer tokens, but only inside (layer ...)
    # and (layers ...) S-expressions — never in string literals or property
    # text.
    new_block = _flip_layer_strings_in_block(block)

    # Step 3: mirror Y of geometry subforms only (fp_line/fp_arc/fp_circle/
    # fp_poly/fp_rect/pad). KiCad rotates (property ...) and (fp_text ...)
    # itself when the side flips, so we must NOT touch them — doing so would
    # corrupt user-positioned Reference/Value/property text on a single flip.
    new_block = _mirror_geometry_subforms(new_block)

    # Step 4: rewrite footprint origin — anchored to the FIRST top-level
    # (at ...) child of the footprint form. Always emit the 3-arg form when a
    # flip happens to make the rotation explicit.
    m_at = _find_first_top_at(new_block)
    if not m_at:
        raise ValueError("footprint (at ...) not found after layer flip")
    new_at = f"(at {_fmt_coord(new_x)} {_fmt_coord(new_y)} {_fmt_coord(new_rot)})"
    new_block = new_block[: m_at.start()] + new_at + new_block[m_at.end() :]

    new_text = text[:open_idx] + new_block + text[end_idx:]

    changed = _maybe_write(pcb_path, text, new_text, dry_run)
    return {
        "action": "move_footprint_layer",
        "changed": changed,
        "diff": _diff(text, new_text, pcb_path),
        "details": {
            "ref": ref,
            "old": {"layer": cur_layer, "x": old_x, "y": old_y, "rotation": old_rot},
            "new": {"layer": target_layer, "x": new_x, "y": new_y, "rotation": new_rot},
            "side": side,
        },
    }


# ---------------------------------------------------------------------------
# import_footprints
# ---------------------------------------------------------------------------
#
# Staging area policy
# -------------------
# Newly added footprints are placed on a deterministic grid OUTSIDE the
# project's V1 floorplan envelope (the floorplan rectangle in
# cupwarmer-hw.kicad_pcb sits roughly between X=80..170, Y=60..160 mm).
# Staging origin: (200, 200) mm. Pitch: 5 mm. Columns per row: 20.
# Refs are sorted alphanumerically (R1, R2, ... U1) for deterministic order.
# This guarantees:
#   * staged footprints never overlap an existing user-placed footprint inside
#     the floorplan
#   * re-running ``pcb sync`` is a no-op (same refs hash to the same
#     slot, but they are already present so they are skipped)
#   * the staging slot for a given ref is stable across runs / output paths


import hashlib
import re as _re
import uuid as _uuid

STAGING_ORIGIN_X = 200.0
STAGING_ORIGIN_Y = 200.0
STAGING_PITCH = 5.0
STAGING_COLS = 20


def _same_file(a: Path, b: Path) -> bool:
    """Robust same-file check across case-sensitive / case-insensitive
    filesystems. Falls back to ``Path.resolve()`` equality when either path
    does not exist or ``os.path.samefile`` raises.
    """
    try:
        if a.exists() and b.exists() and os.path.samefile(a, b):
            return True
    except OSError:
        pass
    return a.resolve() == b.resolve()


def _staging_xy(index: int) -> tuple[float, float]:
    row, col = divmod(index, STAGING_COLS)
    return (
        STAGING_ORIGIN_X + col * STAGING_PITCH,
        STAGING_ORIGIN_Y + row * STAGING_PITCH,
    )


def _det_uuid(seed: str) -> str:
    """Deterministic RFC-4122 v4 UUID derived from a seed.

    Uses md5(seed) as the 16-byte payload and lets ``uuid.UUID(bytes=...,
    version=4)`` set the version (0x40) and variant (0x80) bits correctly.
    """
    digest = hashlib.md5(seed.encode("utf-8")).digest()
    return str(_uuid.UUID(bytes=digest, version=4))


def _ref_sort_key(ref: str) -> tuple[str, int, str]:
    m = _re.match(r"^([A-Za-z]+)(\d+)(.*)$", ref)
    if m:
        return (m.group(1), int(m.group(2)), m.group(3))
    return (ref, 0, "")


def _existing_refs(text: str) -> set[str]:
    refs: set[str] = set()
    for open_idx, end_idx in _iter_top_blocks(text, "footprint"):
        block = text[open_idx:end_idx]
        m = _re.search(r'\(property\s+"Reference"\s+"([^"]+)"', block)
        if m:
            refs.add(m.group(1))
    return refs


def _strip_kicad_mod_header(mod_text: str) -> str:
    """Remove top-level (version ...) (generator ...) (generator_version ...)
    lines from a .kicad_mod so the footprint can be embedded in a board."""
    out_lines: list[str] = []
    for line in mod_text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("(version ") or stripped.startswith(
            "(generator "
        ) or stripped.startswith("(generator_version "):
            # Only drop these when they appear at indent depth 1 (single tab).
            if line.startswith("\t(") or line.startswith("  ("):
                continue
        out_lines.append(line)
    return "".join(out_lines)


def _reindent_footprint_block(mod_text: str) -> str:
    """The .kicad_mod uses 1-tab base indent. Inside .kicad_pcb the footprint
    sits at 1-tab depth too — so every line gets one extra leading tab."""
    out_lines: list[str] = []
    for line in mod_text.splitlines(keepends=True):
        if line.strip() == "":
            out_lines.append(line)
            continue
        out_lines.append("\t" + line)
    return "".join(out_lines)


_PROP_AT_RE = _re.compile(
    r'(\(property\s+"(?P<key>[^"]+)"\s+)"(?P<val>[^"]*)"'
)


def _build_footprint_block(
    *,
    ref: str,
    value: str,
    lib_id: str,
    mod_text: str,
    at_xy: tuple[float, float],
    rotation: float = 0.0,
) -> str:
    """Build a placed-footprint block from a .kicad_mod source.

    Mutations applied to the .kicad_mod text:
      1. The opening ``(footprint "<lib_name>"`` token is replaced with the
         full ``LIB:Name`` lib_id (so KiCad knows which library it came from).
      2. Header lines (version/generator/generator_version) are stripped.
      3. A ``(uuid "...")`` is inserted after ``(layer "...")``.
      4. A ``(at X Y 0)`` is inserted after the new uuid line.
      5. ``(property "Reference" "REF**" ...)`` -> ``"<ref>"``.
      6. ``(property "Value" "<orig>" ...)``    -> ``"<value>"``.
      7. Existing inner uuids inside the footprint are replaced with
         deterministic ones seeded from ``ref + offset`` so we don't reuse
         placeholder uuids and don't introduce non-determinism.
      8. The whole block is re-indented one tab deeper.
    """
    # 1. Header swap: (footprint "<lib_name>" ... -> (footprint "<lib_id>" ...
    text = _re.sub(
        r'^\(footprint\s+"[^"]*"',
        f'(footprint "{lib_id}"',
        mod_text,
        count=1,
    )

    # 2. Strip header lines.
    text = _strip_kicad_mod_header(text)

    # 3. Replace the existing first (uuid "...") inside the .kicad_mod (if any)
    #    or insert one after the first (layer "..."). Either way we end up with
    #    a deterministic top-level footprint uuid.
    fp_uuid = _det_uuid(f"{ref}:fp")
    if _re.search(r'^\t\(uuid\s+"[^"]+"\)', text, _re.MULTILINE):
        text = _re.sub(
            r'^\t\(uuid\s+"[^"]+"\)',
            f'\t(uuid "{fp_uuid}")',
            text,
            count=1,
            flags=_re.MULTILINE,
        )
    else:
        text = _re.sub(
            r'(^\t\(layer\s+"[^"]+"\)\n)',
            r'\1\t(uuid "' + fp_uuid + r'")\n',
            text,
            count=1,
            flags=_re.MULTILINE,
        )

    # 4. Insert (at X Y 0) right after the (uuid ...) line at indent depth 1.
    new_at = f'\t(at {_fmt_coord(at_xy[0])} {_fmt_coord(at_xy[1])} {_fmt_coord(rotation)})\n'
    text = _re.sub(
        r'(^\t\(uuid\s+"[^"]+"\)\n)',
        r'\1' + new_at,
        text,
        count=1,
        flags=_re.MULTILINE,
    )

    # 5/6. Patch Reference / Value property values.
    def _patch_prop(t: str, key: str, new_val: str) -> str:
        return _re.sub(
            r'(\(property\s+"' + _re.escape(key) + r'"\s+)"[^"]*"',
            lambda m: m.group(1) + '"' + new_val.replace('"', '\\"') + '"',
            t,
            count=1,
        )

    text = _patch_prop(text, "Reference", ref)
    if value:
        text = _patch_prop(text, "Value", value)

    # 7. Rewrite every inner (uuid "...") deterministically. We've already
    #    placed the top-level fp uuid; now replace any remaining ones (pad,
    #    fp_line, fp_circle, ...) with seeded values keyed by occurrence.
    counter = {"i": 0}

    def _sub_uuid(m: _re.Match) -> str:
        counter["i"] += 1
        return f'(uuid "{_det_uuid(ref + ":inner:" + str(counter["i"]))}")'

    # Skip the very first uuid (the fp uuid we just set) by counting matches.
    seen = {"first": True}

    def _sub_uuid_skipfirst(m: _re.Match) -> str:
        if seen["first"]:
            seen["first"] = False
            return m.group(0)
        return _sub_uuid(m)

    text = _re.sub(r'\(uuid\s+"[^"]+"\)', _sub_uuid_skipfirst, text)

    # 7b. Bake the footprint rotation into each pad's (at X Y R). KiCad
    #     serializes pad rotation in the .kicad_pcb as absolute
    #     (footprint_rotation + template_pad_rotation); template files only
    #     carry the latter, so without this step a footprint rebuilt from
    #     .kicad_mod onto a non-zero footprint rotation would render with
    #     incorrect pad orientation (rectangular SMD pads pointing along the
    #     wrong axis).
    if rotation:
        # _iter_pad_blocks_in_footprint expects 2-tab indented pads; the
        # un-reindented kicad_mod text has them at 1-tab indent. Match that.
        pad_head_pat = _re.compile(r'^(\t)\(pad\s+"([^"]*)"', _re.MULTILINE)
        out_chunks: list[str] = []
        cursor = 0
        for m in pad_head_pat.finditer(text):
            indent = m.group(1)
            p_open = m.start() + len(indent)
            p_end = _find_block_end(text, p_open)
            pad = text[p_open:p_end]
            at_m = _re.search(
                r'\(at\s+(-?[\d.]+)\s+(-?[\d.]+)((?:\s+-?[\d.]+)?)\s*\)',
                pad,
            )
            if at_m:
                x = float(at_m.group(1))
                y = float(at_m.group(2))
                rest = at_m.group(3).strip()
                existing_rot = float(rest) if rest else 0.0
                new_rot = (existing_rot + float(rotation)) % 360
                if new_rot >= 180:
                    new_rot -= 360
                new_at = (
                    f"(at {_fmt_coord(x)} {_fmt_coord(y)} {_fmt_coord(new_rot)})"
                )
                pad = pad[: at_m.start()] + new_at + pad[at_m.end() :]
            out_chunks.append(text[cursor:p_open])
            out_chunks.append(pad)
            cursor = p_end
        out_chunks.append(text[cursor:])
        text = "".join(out_chunks)

    # 8. Re-indent.
    text = _reindent_footprint_block(text)

    # Ensure the block ends with a single trailing newline.
    if not text.endswith("\n"):
        text += "\n"
    return text


def _add_missing_footprints_in_memory(
    text: str,
    pcb_path: Path,
    schematic_netlist_path: Path,
    project_dir: Path,
) -> tuple[str, dict[str, Any]]:
    """Pure in-memory: take board text + schematic netlist, return updated text
    plus a summary dict of additions. No I/O on the board file itself."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pcb_netlist  # noqa: WPS433

    existing = _existing_refs(text)
    components = pcb_netlist.parse_components(schematic_netlist_path)
    sch_refs = sorted(components.keys(), key=_ref_sort_key)

    missing: list[str] = [r for r in sch_refs if r not in existing]
    skipped_no_footprint: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []
    added: list[dict[str, Any]] = []

    new_blocks: list[str] = []
    for idx, ref in enumerate(missing):
        comp = components[ref]
        lib_id = comp["footprint"]
        if not lib_id:
            skipped_no_footprint.append({"ref": ref})
            continue
        mod_path = pcb_netlist.resolve_footprint_path(lib_id, project_dir)
        if mod_path is None:
            unresolved.append({"ref": ref, "lib_id": lib_id})
            continue
        mod_text = mod_path.read_text(encoding="utf-8")
        xy = _staging_xy(idx)
        block = _build_footprint_block(
            ref=ref,
            value=comp.get("value", ""),
            lib_id=lib_id,
            mod_text=mod_text,
            at_xy=xy,
        )
        link_text = _link_subforms_from_comp(comp)
        if link_text:
            block = _inject_link_subforms(block, link_text)
        new_blocks.append(block)
        added.append({
            "ref": ref,
            "lib_id": lib_id,
            "value": comp.get("value", ""),
            "at": {"x": xy[0], "y": xy[1]},
            "source": str(mod_path),
        })

    if new_blocks:
        m = _re.search(r"\)\s*\Z", text)
        if not m:
            raise ValueError("could not locate closing paren of kicad_pcb form")
        insert_at = m.start()
        new_text = text[:insert_at] + "".join(new_blocks) + text[insert_at:]
    else:
        new_text = text

    after_refs = _existing_refs(new_text)
    parity_missing = sorted(set(sch_refs) - after_refs, key=_ref_sort_key)
    parity_extra = sorted(after_refs - set(sch_refs), key=_ref_sort_key)
    parity_clean = not parity_missing and not unresolved

    summary = {
        "schematic_refs": len(sch_refs),
        "existing_refs": len(existing),
        "added": added,
        "skipped_no_footprint": skipped_no_footprint,
        "unresolved": unresolved,
        "staging": {
            "origin": [STAGING_ORIGIN_X, STAGING_ORIGIN_Y],
            "pitch": STAGING_PITCH,
            "cols": STAGING_COLS,
        },
        "parity": {
            "clean": parity_clean,
            "missing_on_board": parity_missing,
            "extra_on_board": parity_extra,
        },
    }
    return new_text, summary


def import_footprints(
    pcb_path: str | Path,
    schematic_netlist_path: str | Path,
    *,
    project_dir: str | Path | None = None,
    output_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Add missing schematic footprints to a board.

    Existing footprints (and their placement) are preserved. New footprints are
    appended just before the closing ``)`` of the ``(kicad_pcb ...)`` form, on
    a deterministic staging grid outside the floorplan.

    With ``output_path`` set, the input ``pcb_path`` is left untouched and the
    new content is written to ``output_path``. Without ``output_path`` the
    write is in place. ``dry_run`` skips writes entirely.
    """
    pcb_path = Path(pcb_path)
    schematic_netlist_path = Path(schematic_netlist_path)
    project_dir = Path(project_dir) if project_dir is not None else pcb_path.parent

    text = _read(pcb_path)
    new_text, summary = _add_missing_footprints_in_memory(
        text, pcb_path, schematic_netlist_path, project_dir
    )

    diff = _diff(text, new_text, pcb_path)

    target = Path(output_path) if output_path is not None else pcb_path
    changed = text != new_text
    wrote = False
    # Write contract:
    #   - dry_run -> never write.
    #   - in-place (output_path is None or == pcb_path) -> write iff changed.
    #   - distinct output_path -> always write (caller asked for the copy),
    #     but ``changed`` still reflects whether content actually differs.
    if not dry_run:
        in_place = output_path is None or _same_file(target, pcb_path)
        if in_place:
            if changed:
                pcb_path.write_text(new_text, encoding="utf-8")
                wrote = True
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_text, encoding="utf-8")
            wrote = True

    return {
        "action": "import_footprints",
        "changed": text != new_text,
        "wrote": wrote,
        "target": str(target),
        "input_board": str(pcb_path),
        "diff": diff,
        "details": summary,
    }


# ---------------------------------------------------------------------------
# sync_from_schematic
# ---------------------------------------------------------------------------


_PAD_HEAD_RE = _re.compile(r'^(\t\t)\(pad\s+"([^"]*)"', _re.MULTILINE)
_PAD_NET_LINE_RE = _re.compile(
    r'^(\t+)\(net\s+(?:\d+\s+)?"([^"]*)"\s*\)\s*\n', _re.MULTILINE
)


def _iter_pad_blocks_in_footprint(fp_block: str):
    """Yield (open_idx, end_idx, pin_name, indent) for each ``(pad ...)``
    subblock inside a footprint block. ``indent`` is the leading whitespace
    (typically ``\\t\\t``) of the ``(pad`` line."""
    for m in _PAD_HEAD_RE.finditer(fp_block):
        indent = m.group(1)
        pin = m.group(2)
        open_idx = m.start() + len(indent)
        end_idx = _find_block_end(fp_block, open_idx)
        yield open_idx, end_idx, pin, indent


def _pad_old_net(pad_text: str) -> str | None:
    """Return the net name currently set on the pad, or None if absent."""
    m = _PAD_NET_LINE_RE.search(pad_text)
    return m.group(2) if m else None


def _rewrite_pad_net(pad_text: str, indent: str, new_net: str) -> tuple[str, str | None, bool]:
    """Rewrite or insert the ``(net "...")`` line of a pad.

    ``indent`` is the indent of the ``(pad`` head; the inner net line uses
    ``indent + '\\t'``. Returns (new_pad_text, old_net_or_None, inserted_bool).
    """
    inner_indent = indent + "\t"
    new_line = f'{inner_indent}(net "{new_net}")\n'

    m = _PAD_NET_LINE_RE.search(pad_text)
    if m:
        old = m.group(2)
        if old == new_net:
            return pad_text, old, False
        new_text = pad_text[: m.start()] + new_line + pad_text[m.end():]
        return new_text, old, False

    # No (net ...) line — insert just before the (uuid ...) line.
    uuid_pat = _re.compile(r'^(\t+)\(uuid\s+"[^"]+"\)\s*\n', _re.MULTILINE)
    um = uuid_pat.search(pad_text)
    if um:
        new_text = pad_text[: um.start()] + new_line + pad_text[um.start():]
        return new_text, None, True

    # Fallback: pad has neither (uuid ...) nor an existing (net ...) line.
    # Reformat the closing region to multi-line layout — body, then the
    # injected net on its own line, then the closing paren on its own line —
    # so subsequent syncs can find/update the net via _PAD_NET_LINE_RE
    # instead of inserting a duplicate.
    close = pad_text.rstrip()
    if not close.endswith(")"):
        return pad_text, None, False
    close_idx = pad_text.rfind(")")
    body = pad_text[:close_idx].rstrip()
    new_text = body + "\n" + new_line + indent + ")"
    return new_text, None, True


def _collect_pad_nets(text: str) -> set[str]:
    """Return the set of net names referenced by any pad anywhere in ``text``."""
    out: set[str] = set()
    for open_idx, end_idx in _iter_top_blocks(text, "footprint"):
        fp = text[open_idx:end_idx]
        for p_open, p_end, _pin, _indent in _iter_pad_blocks_in_footprint(fp):
            pad = fp[p_open:p_end]
            n = _pad_old_net(pad)
            if n is not None:
                out.add(n)
    return out


_FP_HEAD_LIBID_RE = _re.compile(r'^\(footprint\s+"([^"]*)"((?:\s+[a-z_]+)?)')


_LINK_HEADS = ("path", "sheetname", "sheetfile")


def _link_subforms_from_comp(comp: dict[str, Any]) -> str:
    """Build the ``(path ...)`` / ``(sheetname ...)`` / ``(sheetfile ...)``
    text block for a footprint, derived from netlist comp metadata. Returns ""
    if the comp is missing any of the required identifiers (linkage cannot be
    constructed, e.g. for hand-drawn netlists)."""
    sheet_tstamps = comp.get("sheet_tstamps", "") or ""
    comp_tstamps = comp.get("tstamps", "") or ""
    sheet_names = comp.get("sheet_names", "") or ""
    sheetfile = comp.get("sheetfile", "") or ""
    if not (sheet_tstamps and comp_tstamps and sheet_names and sheetfile):
        return ""
    # KiCad's path field concatenates sheetpath/tstamps (already contains
    # leading + trailing slashes) with the component's tstamps. Guard against
    # a malformed sheet_tstamps that lacks the trailing slash.
    sep = "" if sheet_tstamps.endswith("/") else "/"
    path_val = f"{sheet_tstamps}{sep}{comp_tstamps}"
    return (
        f'\t\t(path "{path_val}")\n'
        f'\t\t(sheetname "{sheet_names}")\n'
        f'\t\t(sheetfile "{sheetfile}")\n'
    )


def _extract_link_subforms(block: str) -> str:
    """Return the concatenated text of (path ...), (sheetname ...), and
    (sheetfile ...) top-level subforms of a footprint block, with a trailing
    newline after each. These fields link a placed footprint to the matching
    schematic instance; they must be carried across when the footprint is
    rebuilt from a .kicad_mod template (e.g. during a lib_id swap), otherwise
    KiCad treats the footprint as orphaned and re-adds it from the schematic.
    """
    body_start = _footprint_head_end(block)
    chunks: list[str] = []
    for head, open_idx, end_idx in _iter_subforms(block, body_start):
        if head in _LINK_HEADS:
            # Include the leading indent (tabs/spaces) of the subform's line so
            # the re-injected block keeps consistent indentation.
            line_start = block.rfind("\n", 0, open_idx) + 1
            chunks.append(block[line_start:end_idx] + "\n")
    return "".join(chunks)


def _inject_link_subforms(new_block: str, link_text: str) -> str:
    """Insert link_text (path/sheetname/sheetfile lines) into new_block right
    after the last (property ...) subform. If new_block already has any of
    these subforms, returns new_block unchanged.
    """
    if not link_text:
        return new_block
    # _build_footprint_block emits a leading tab so the splice lines up with
    # surrounding pcb subforms; _footprint_head_end expects the head at
    # offset 0, so locate the actual "(footprint" first.
    fp_off = new_block.find("(footprint")
    if fp_off < 0:
        return new_block
    inner = new_block[fp_off:]
    body_start_inner = _footprint_head_end(inner)
    body_start = fp_off + body_start_inner
    last_prop_end = -1
    for head, open_idx, end_idx in _iter_subforms(new_block, body_start):
        if head in _LINK_HEADS:
            return new_block
        if head == "property":
            last_prop_end = end_idx
    if last_prop_end < 0:
        return new_block
    # Step past the trailing newline after the last property line so the
    # injected block lines up with the surrounding subform indentation.
    insert_at = last_prop_end
    if insert_at < len(new_block) and new_block[insert_at] == "\n":
        insert_at += 1
    return new_block[:insert_at] + link_text + new_block[insert_at:]


def _remove_link_subforms(block: str) -> str:
    """Drop any (path ...) / (sheetname ...) / (sheetfile ...) top-level
    subforms from a footprint block. Used together with _inject_link_subforms
    to re-set the link to a canonical value."""
    fp_off = block.find("(footprint")
    if fp_off < 0:
        return block
    inner_start = fp_off + _footprint_head_end(block[fp_off:])
    spans: list[tuple[int, int]] = []
    for head, open_idx, end_idx in _iter_subforms(block, inner_start):
        if head in _LINK_HEADS:
            line_start = block.rfind("\n", 0, open_idx) + 1
            line_end = end_idx
            if line_end < len(block) and block[line_end] == "\n":
                line_end += 1
            spans.append((line_start, line_end))
    if not spans:
        return block
    out: list[str] = []
    cursor = 0
    for s, e in spans:
        out.append(block[cursor:s])
        cursor = e
    out.append(block[cursor:])
    return "".join(out)


def _property_keys_in_block(block: str) -> list[str]:
    """List of (property "<key>" ...) keys found at the top level of a
    footprint block, in order."""
    work = block.lstrip("\t")
    body_start = _footprint_head_end(work)
    keys: list[str] = []
    for head, oi, ei in _iter_subforms(work, body_start):
        if head == "property":
            m = _re.match(r'\(property\s+"([^"]+)"', work[oi:ei])
            if m:
                keys.append(m.group(1))
    return keys


def _extract_property_map(block: str) -> dict[str, str]:
    """Map property key -> full ``(property "K" "V" ...)`` subform text from
    a footprint block. Used to carry per-property placement / effects / layer
    / hide flags across a footprint rebuild."""
    work = block.lstrip("\t")
    body_start = _footprint_head_end(work)
    out: dict[str, str] = {}
    for head, oi, ei in _iter_subforms(work, body_start):
        if head != "property":
            continue
        m = _re.match(r'\(property\s+"([^"]+)"', work[oi:ei])
        if m:
            out[m.group(1)] = work[oi:ei]
    return out


def _preserve_template_property_placements(
    fresh_block: str, old_block: str, template_keys: set[str]
) -> str:
    """For each template-key property present in both blocks, replace the
    subform in ``fresh_block`` with the one from ``old_block`` (patched so its
    value string matches what fresh has). Carries over user-tweaked
    ``(at ...)`` / ``(layer ...)`` / ``(effects ...)`` / ``(hide ...)`` for
    Reference, Value, Datasheet, etc. — KiCad GUI's
    "Update Footprints from Library" preserves these by default and we should
    too."""
    old_props = _extract_property_map(old_block)
    if not old_props:
        return fresh_block
    had_tab = fresh_block.startswith("\t")
    work = fresh_block[1:] if had_tab else fresh_block
    body_start = _footprint_head_end(work)
    out_parts: list[str] = [work[:body_start]]
    cursor = body_start
    for head, oi, ei in _iter_subforms(work, body_start):
        if head != "property":
            continue
        m = _re.match(r'\(property\s+"([^"]+)"\s+"([^"]*)"', work[oi:ei])
        if not m:
            continue
        key = m.group(1)
        if key not in template_keys or key not in old_props:
            continue
        new_value = m.group(2)
        patched = _re.sub(
            r'(\(property\s+"' + _re.escape(key) + r'"\s+)"[^"]*"',
            lambda mm: mm.group(1) + '"' + new_value.replace('"', '\\"') + '"',
            old_props[key],
            count=1,
        )
        out_parts.append(work[cursor:oi])
        out_parts.append(patched)
        cursor = ei
    out_parts.append(work[cursor:])
    s = "".join(out_parts)
    return ("\t" + s) if had_tab else s


def _extract_user_property_subforms(block: str, template_keys: set[str]) -> str:
    """Concatenated text of (property "<key>" ...) subforms whose key is NOT
    in ``template_keys`` (i.e. user-added properties that the .kicad_mod
    template does not provide). Each chunk includes its leading line indent
    and a trailing newline so it splices cleanly."""
    work = block.lstrip("\t")
    body_start = _footprint_head_end(work)
    chunks: list[str] = []
    for head, oi, ei in _iter_subforms(work, body_start):
        if head != "property":
            continue
        m = _re.match(r'\(property\s+"([^"]+)"', work[oi:ei])
        if not m or m.group(1) in template_keys:
            continue
        line_start = work.rfind("\n", 0, oi) + 1
        chunks.append(work[line_start:ei] + "\n")
    return "".join(chunks)


def _inject_user_properties(new_block: str, props_text: str) -> str:
    """Insert ``props_text`` after the last (property ...) form in
    ``new_block``. If new_block already contains any (path/sheetname/sheetfile)
    link form, props are still inserted before it (after the last property
    that precedes the link). No-op when ``props_text`` is empty."""
    if not props_text:
        return new_block
    fp_off = new_block.find("(footprint")
    if fp_off < 0:
        return new_block
    inner = new_block[fp_off:]
    body_start_inner = _footprint_head_end(inner)
    body_start = fp_off + body_start_inner
    last_prop_end = -1
    for head, oi, ei in _iter_subforms(new_block, body_start):
        if head == "property":
            last_prop_end = ei
        elif head in _LINK_HEADS:
            break
    if last_prop_end < 0:
        return new_block
    insert_at = last_prop_end
    if insert_at < len(new_block) and new_block[insert_at] == "\n":
        insert_at += 1
    return new_block[:insert_at] + props_text + new_block[insert_at:]


def _extract_pad_net_map(fp_block: str) -> dict[str, str]:
    """Return ``pin_name -> net_name`` for pads that currently have a
    (net ...) line. Pads without a net (NC) are omitted."""
    out: dict[str, str] = {}
    for p_open, p_end, pin, _indent in _iter_pad_blocks_in_footprint(fp_block):
        n = _pad_old_net(fp_block[p_open:p_end])
        if n is not None:
            out[pin] = n
    return out


def _apply_pad_net_map(fp_block: str, pin_to_net: dict[str, str]) -> str:
    """For each pad in ``fp_block``, set its (net ...) to ``pin_to_net[pin]``
    when present. Pads not in the map are left as-is."""
    out_chunks: list[str] = []
    cursor = 0
    for p_open, p_end, pin, indent in _iter_pad_blocks_in_footprint(fp_block):
        out_chunks.append(fp_block[cursor:p_open])
        pad_text = fp_block[p_open:p_end]
        net = pin_to_net.get(pin)
        if net:
            new_pad, _, _ = _rewrite_pad_net(pad_text, indent, net)
            out_chunks.append(new_pad)
        else:
            out_chunks.append(pad_text)
        cursor = p_end
    out_chunks.append(fp_block[cursor:])
    return "".join(out_chunks)


_UUID_LINE_RE = _re.compile(r'\(uuid\s+"[^"]*"\)')


def _normalize_for_refresh_compare(block: str, template_keys: set[str]) -> str:
    """Strip instance-specific content from a footprint block so two blocks
    can be compared structurally. Removes uuids, pad (net ...) lines, link
    subforms, and user-added properties; collapses whitespace runs."""
    s = block.lstrip("\t")
    s = _UUID_LINE_RE.sub('(uuid "")', s)
    s = _PAD_NET_LINE_RE.sub('', s)
    s = _remove_link_subforms(s)
    body_start = _footprint_head_end(s)
    out_parts: list[str] = [s[:body_start]]
    cursor = body_start
    for head, oi, ei in _iter_subforms(s, body_start):
        if head != "property":
            continue
        # Drop ALL property subforms from the comparison: placement / effects
        # / layer / hide are preserved across rebuild via
        # _preserve_template_property_placements (template keys) and
        # _inject_user_properties (user keys), so a property-only divergence
        # would not change the rebuilt block and should not trigger refresh.
        # ``template_keys`` is unused here but kept in the signature for
        # callers passing it through.
        line_start = s.rfind("\n", 0, oi) + 1
        line_end = ei
        if line_end < len(s) and s[line_end] == "\n":
            line_end += 1
        out_parts.append(s[cursor:line_start])
        cursor = line_end
    out_parts.append(s[cursor:])
    s = "".join(out_parts)
    # Collapse all whitespace runs to nothing — refreshed/inserted blocks may
    # carry quirky indentation (e.g. legacy pad-net inserts) that we don't
    # want to flag as a body-divergence.
    return _re.sub(r'\s+', '', s)


def _swap_footprint_libs_in_memory(
    text: str,
    components: dict[str, dict[str, Any]],
    project_dir: Path,
) -> tuple[str, dict[str, Any]]:
    """Swap each on-board footprint's lib_id to match the schematic's
    Footprint property when they differ. Preserves position (x/y/rotation),
    board side (F.Cu/B.Cu), and uses deterministic UUIDs seeded from ref.
    Pad nets are NOT preserved — the pad-net step that runs after this fixes
    them. Locked footprints are refused.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pcb_netlist  # noqa: WPS433

    swapped: list[dict[str, Any]] = []
    swap_skipped: list[dict[str, Any]] = []

    # Iterate top-level footprint blocks. Because we mutate text length, we
    # collect spans first, then rewrite right-to-left so earlier offsets stay
    # valid.
    spans = list(_iter_top_blocks(text, "footprint"))
    new_text = text
    for open_idx, end_idx in reversed(spans):
        block = new_text[open_idx:end_idx]
        m_ref = _re.search(r'\(property\s+"Reference"\s+"([^"]+)"', block)
        if not m_ref:
            continue
        ref = m_ref.group(1)

        comp = components.get(ref)
        if comp is None:
            # Footprint on board not in schematic — leave alone (parity check
            # reports this elsewhere).
            continue

        m_head = _FP_HEAD_LIBID_RE.match(block)
        if not m_head:
            continue
        cur_lib_id = m_head.group(1)

        new_lib_id = comp.get("footprint", "") or ""
        if not new_lib_id:
            swap_skipped.append({"ref": ref, "reason": "no_schematic_footprint"})
            continue
        if new_lib_id == cur_lib_id:
            continue

        if _is_locked(block):
            swap_skipped.append({
                "ref": ref,
                "reason": "locked",
                "old_lib_id": cur_lib_id,
                "new_lib_id": new_lib_id,
            })
            continue

        mod_path = pcb_netlist.resolve_footprint_path(new_lib_id, project_dir)
        if mod_path is None:
            swap_skipped.append({
                "ref": ref,
                "reason": "unresolved",
                "old_lib_id": cur_lib_id,
                "new_lib_id": new_lib_id,
            })
            continue

        cur_at = _block_at(block)
        if cur_at is None:
            swap_skipped.append({
                "ref": ref,
                "reason": "no_at",
                "old_lib_id": cur_lib_id,
                "new_lib_id": new_lib_id,
            })
            continue
        old_x, old_y, old_rot = cur_at
        cur_layer = _block_layer(block) or "F.Cu"

        mod_text = mod_path.read_text(encoding="utf-8")
        new_block = _build_footprint_block(
            ref=ref,
            value=comp.get("value", ""),
            lib_id=new_lib_id,
            mod_text=mod_text,
            at_xy=(old_x, old_y),
            rotation=old_rot,
        )

        if cur_layer == "B.Cu":
            new_block = _flip_layer_strings_in_block(new_block)
            new_block = _mirror_geometry_subforms(new_block)

        # Carry over template-property placements (Reference / Value /
        # Datasheet / Description / Footprint) from the existing footprint:
        # at / layer / effects / hide. KiCad GUI's "Update Footprints from
        # Library" preserves these by default, and resetting them to the
        # .kicad_mod default would lose user-positioned ref text.
        template_keys = set(_property_keys_in_block(new_block))
        new_block = _preserve_template_property_placements(
            new_block, block, template_keys
        )

        # Carry over the schematic-link subforms (path / sheetname / sheetfile)
        # from the existing footprint. _build_footprint_block synthesises a
        # fresh block from the .kicad_mod template, which has none of these.
        # Fall back to deriving them from the netlist when the existing block
        # is missing them (e.g. legacy boards), so a swap also retrofits the
        # link instead of just preserving its absence.
        link_text = _extract_link_subforms(block) or _link_subforms_from_comp(comp)
        new_block = _inject_link_subforms(new_block, link_text)

        # The existing block sits after a leading tab at open_idx-1; the new
        # block from _reindent_footprint_block already starts with that tab.
        # Splice over the leading tab as well so we don't double-indent.
        splice_start = open_idx - 1 if open_idx > 0 and new_text[open_idx - 1] == "\t" else open_idx
        new_text = new_text[:splice_start] + new_block + new_text[end_idx:]
        swapped.append({
            "ref": ref,
            "old_lib_id": cur_lib_id,
            "new_lib_id": new_lib_id,
            "at": {"x": old_x, "y": old_y, "rotation": old_rot},
            "side": cur_layer,
        })

    # Reverse swapped to maintain natural order (we built it right-to-left).
    swapped.reverse()
    swap_skipped.reverse()
    return new_text, {"swapped": swapped, "swap_skipped": swap_skipped}


def _refresh_footprints_in_memory(
    text: str,
    components: dict[str, dict[str, Any]],
    project_dir: Path,
    exclude_refs: set[str],
) -> tuple[str, dict[str, Any]]:
    """Rebuild on-board footprints from their .kicad_mod template when their
    body diverges from the library (KiCad GUI's "Update Footprints from
    Library"). Skips refs in ``exclude_refs`` (typically just-added /
    just-swapped) and footprints whose lib_id does not match the schematic.

    Preserved per refresh: position/rotation/layer, schematic-link subforms
    (path/sheetname/sheetfile), pad nets (by pin name), and any property whose
    key is not present in the template (e.g. Manufacturer / LCSC / MPN).
    Locked footprints are skipped.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pcb_netlist  # noqa: WPS433

    refreshed: list[dict[str, Any]] = []
    refresh_skipped: list[dict[str, Any]] = []

    spans = list(_iter_top_blocks(text, "footprint"))
    new_text = text
    for open_idx, end_idx in reversed(spans):
        block = new_text[open_idx:end_idx]
        m_ref = _re.search(r'\(property\s+"Reference"\s+"([^"]+)"', block)
        if not m_ref:
            continue
        ref = m_ref.group(1)
        if ref in exclude_refs:
            continue

        comp = components.get(ref)
        if comp is None:
            continue
        lib_id = comp.get("footprint", "") or ""
        if not lib_id:
            continue

        m_head = _FP_HEAD_LIBID_RE.match(block)
        if not m_head:
            continue
        cur_lib_id = m_head.group(1)
        if cur_lib_id != lib_id:
            # Lib_id divergence is the swap step's responsibility; if we get
            # here that step skipped the swap, so don't refresh either.
            continue

        if _is_locked(block):
            refresh_skipped.append({"ref": ref, "reason": "locked"})
            continue

        mod_path = pcb_netlist.resolve_footprint_path(lib_id, project_dir)
        if mod_path is None:
            refresh_skipped.append({"ref": ref, "reason": "unresolved", "lib_id": lib_id})
            continue

        cur_at = _block_at(block)
        if cur_at is None:
            refresh_skipped.append({"ref": ref, "reason": "no_at"})
            continue
        old_x, old_y, old_rot = cur_at
        cur_layer = _block_layer(block) or "F.Cu"

        mod_text = mod_path.read_text(encoding="utf-8")
        fresh_block = _build_footprint_block(
            ref=ref,
            value=comp.get("value", ""),
            lib_id=lib_id,
            mod_text=mod_text,
            at_xy=(old_x, old_y),
            rotation=old_rot,
        )
        if cur_layer == "B.Cu":
            fresh_block = _flip_layer_strings_in_block(fresh_block)
            fresh_block = _mirror_geometry_subforms(fresh_block)

        template_keys = set(_property_keys_in_block(fresh_block))

        # No semantic difference → skip refresh.
        if (
            _normalize_for_refresh_compare(block, template_keys)
            == _normalize_for_refresh_compare(fresh_block, template_keys)
        ):
            continue

        link_text = _extract_link_subforms(block) or _link_subforms_from_comp(comp)
        user_props = _extract_user_property_subforms(block, template_keys)
        pin_to_net = _extract_pad_net_map(block)

        # Carry over template-property placements first so the property
        # bodies match the user's positioning before user-property and link
        # injection (which insert subforms relative to the property block).
        refreshed_block = _preserve_template_property_placements(
            fresh_block, block, template_keys
        )
        refreshed_block = _inject_user_properties(refreshed_block, user_props)
        refreshed_block = _inject_link_subforms(refreshed_block, link_text)
        refreshed_block = _apply_pad_net_map(refreshed_block, pin_to_net)

        splice_start = (
            open_idx - 1
            if open_idx > 0 and new_text[open_idx - 1] == "\t"
            else open_idx
        )
        new_text = new_text[:splice_start] + refreshed_block + new_text[end_idx:]

        preserved_keys = [
            k for k in _property_keys_in_block(block) if k not in template_keys
        ]
        refreshed.append({
            "ref": ref,
            "lib_id": lib_id,
            "at": {"x": old_x, "y": old_y, "rotation": old_rot},
            "side": cur_layer,
            "preserved_user_properties": preserved_keys,
            "preserved_pad_nets": len(pin_to_net),
        })

    refreshed.reverse()
    refresh_skipped.reverse()
    return new_text, {"refreshed": refreshed, "refresh_skipped": refresh_skipped}


def sync_from_schematic(
    pcb_path: str | Path,
    schematic_netlist_path: str | Path,
    *,
    project_dir: str | Path | None = None,
    output_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Add missing footprints AND update each pad's ``(net "...")`` to match
    the schematic netlist. Idempotent.

    Tracks/vias/zones are NOT touched. Footprints on the board that are not in
    the schematic are reported via the parity summary as ``extra_on_board`` —
    they are not removed.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pcb_netlist  # noqa: WPS433

    pcb_path = Path(pcb_path)
    schematic_netlist_path = Path(schematic_netlist_path)
    project_dir = Path(project_dir) if project_dir is not None else pcb_path.parent

    text = _read(pcb_path)

    # Step 1: add missing footprints in memory.
    after_add_text, add_summary = _add_missing_footprints_in_memory(
        text, pcb_path, schematic_netlist_path, project_dir
    )

    # Step 2: swap footprints whose schematic Footprint property changed.
    components = pcb_netlist.parse_components(schematic_netlist_path)
    after_swap_text, swap_summary = _swap_footprint_libs_in_memory(
        after_add_text, components, project_dir
    )

    # Step 3: refresh footprint bodies that diverge from their .kicad_mod
    # template (KiCad GUI's "Update Footprints from Library"). Skip refs we
    # just added or swapped — those are already canonical.
    handled = {a["ref"] for a in add_summary["added"]} | {
        s["ref"] for s in swap_summary["swapped"]
    }
    after_refresh_text, refresh_summary = _refresh_footprints_in_memory(
        after_swap_text, components, project_dir, handled
    )

    # Step 4: update pad net assignments based on schematic netlist.
    membership = pcb_netlist.parse_net_membership(schematic_netlist_path)
    pad_to_net: dict[tuple[str, str], str] = {}
    for net_name, members in membership.items():
        for ref, pin in members:
            pad_to_net[(ref, pin)] = net_name

    nets_before = _collect_pad_nets(after_refresh_text)

    pad_net_changes: list[dict[str, str]] = []
    pad_net_added: list[dict[str, str]] = []
    link_retrofitted: list[dict[str, str]] = []

    # Walk every footprint block, rebuild it with rewritten pad nets.
    out_chunks: list[str] = []
    cursor = 0
    for open_idx, end_idx in _iter_top_blocks(after_refresh_text, "footprint"):
        fp_block = after_refresh_text[open_idx:end_idx]
        m_ref = _re.search(r'\(property\s+"Reference"\s+"([^"]+)"', fp_block)
        if not m_ref:
            continue
        ref = m_ref.group(1)

        # Rewrite each pad inside this footprint block.
        new_fp_chunks: list[str] = []
        fp_cursor = 0
        for p_open, p_end, pin, indent in _iter_pad_blocks_in_footprint(fp_block):
            new_fp_chunks.append(fp_block[fp_cursor:p_open])
            pad_text = fp_block[p_open:p_end]

            target_net = pad_to_net.get((ref, pin))
            if target_net is None:
                # Pad not in netlist (NC) — leave alone.
                new_fp_chunks.append(pad_text)
            else:
                new_pad, old_net, inserted = _rewrite_pad_net(pad_text, indent, target_net)
                if inserted:
                    pad_net_added.append({"ref": ref, "pad": pin, "net": target_net})
                elif old_net is not None and old_net != target_net:
                    pad_net_changes.append({
                        "ref": ref, "pad": pin, "old": old_net, "new": target_net,
                    })
                new_fp_chunks.append(new_pad)
            fp_cursor = p_end
        new_fp_chunks.append(fp_block[fp_cursor:])
        new_fp_block = "".join(new_fp_chunks)

        # Retrofit / canonicalise the schematic-link subforms (path /
        # sheetname / sheetfile). Older boards (and footprints added by
        # earlier versions of pcb sync) may be missing these; without them
        # KiCad treats the footprint as orphaned and "Update PCB from
        # Schematic" re-adds a duplicate.
        comp = components.get(ref)
        if comp is not None:
            expected_link = _link_subforms_from_comp(comp)
            current_link = _extract_link_subforms(new_fp_block)
            if expected_link and expected_link != current_link:
                cleaned = _remove_link_subforms(new_fp_block)
                new_fp_block = _inject_link_subforms(cleaned, expected_link)
                link_retrofitted.append({
                    "ref": ref,
                    "had_link": "yes" if current_link else "no",
                })

        out_chunks.append(after_refresh_text[cursor:open_idx])
        out_chunks.append(new_fp_block)
        cursor = end_idx
    out_chunks.append(after_refresh_text[cursor:])
    new_text = "".join(out_chunks)

    nets_after = _collect_pad_nets(new_text)
    orphaned_nets = sorted(nets_before - nets_after)

    diff = _diff(text, new_text, pcb_path)

    target = Path(output_path) if output_path is not None else pcb_path
    changed = text != new_text
    wrote = False
    if not dry_run:
        in_place = output_path is None or _same_file(target, pcb_path)
        if in_place:
            if changed:
                pcb_path.write_text(new_text, encoding="utf-8")
                wrote = True
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_text, encoding="utf-8")
            wrote = True

    details = dict(add_summary)
    details["swapped"] = swap_summary["swapped"]
    details["swap_skipped"] = swap_summary["swap_skipped"]
    details["refreshed"] = refresh_summary["refreshed"]
    details["refresh_skipped"] = refresh_summary["refresh_skipped"]
    details["pad_net_changes"] = pad_net_changes
    details["pad_net_added"] = pad_net_added
    details["link_retrofitted"] = link_retrofitted
    details["orphaned_nets"] = orphaned_nets

    return {
        "action": "sync_from_schematic",
        "changed": changed,
        "wrote": wrote,
        "target": str(target),
        "input_board": str(pcb_path),
        "diff": diff,
        "details": details,
    }


def delete_footprint(
    pcb_path: str | Path,
    ref: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a footprint from a PCB by reference.

    Removes the entire ``(footprint ...)`` top-level block whose Reference
    property equals ``ref``. Locked footprints are refused. Tracks/vias/zones
    are NOT touched — caller is responsible for cleaning up any newly
    orphaned copper if necessary.
    """
    pcb_path = Path(pcb_path)
    text = _read(pcb_path)

    found = _find_footprint_block_by_ref(text, ref)
    if not found:
        raise ValueError(f"footprint with reference {ref!r} not found in {pcb_path}")
    open_idx, end_idx, block = found

    if _is_locked(block):
        raise ValueError(f"footprint {ref!r} is locked; refusing to delete")

    # Trim leading tab and trailing newline so the surrounding formatting
    # collapses cleanly. Top-level footprint blocks are emitted as
    # "\t(footprint ...)\n" — drop the leading tab on the line and the
    # trailing newline after the close paren if present.
    line_start = text.rfind("\n", 0, open_idx) + 1
    leading_ws = text[line_start:open_idx]
    cut_start = open_idx - len(leading_ws) if leading_ws.strip() == "" else open_idx
    cut_end = end_idx
    if cut_end < len(text) and text[cut_end] == "\n":
        cut_end += 1

    new_text = text[:cut_start] + text[cut_end:]

    cur_at = _block_at(block)
    cur_layer = _block_layer(block)

    changed = _maybe_write(pcb_path, text, new_text, dry_run)
    return {
        "action": "delete_footprint",
        "changed": changed,
        "diff": _diff(text, new_text, pcb_path),
        "details": {
            "ref": ref,
            "at": (
                {"x": cur_at[0], "y": cur_at[1], "rotation": cur_at[2]}
                if cur_at
                else None
            ),
            "layer": cur_layer,
        },
    }


# ---------------------------------------------------------------------------
# Zone helpers (shared by query/edit zone commands)
# ---------------------------------------------------------------------------


def _iter_zone_blocks(text: str):
    """Yield (open_idx, end_idx, block_text) for every top-level (zone ...)
    block.
    """
    for open_idx, end_idx in _iter_top_blocks(text, "zone"):
        yield open_idx, end_idx, text[open_idx:end_idx]


def _zone_field(block: str, key: str) -> str | None:
    """Extract a single-value field from a zone block.

    Handles both quoted ``(key "value")`` and bare ``(key value)`` forms.
    Returns the raw (unquoted) string value, or None if absent.

    Only matches subforms at the top level of the zone block (depth 1
    relative to the zone), so nested keys inside e.g. ``(fill ...)`` are
    not accidentally picked up.
    """
    if not block.startswith("(zone"):
        return None
    # Body starts right after "(zone".
    body_start = len("(zone")
    n = len(block)
    i = body_start
    pat_quoted = re.compile(r"\(" + re.escape(key) + r'\s+"((?:[^"\\]|\\.)*)"\s*\)')
    pat_bare = re.compile(r"\(" + re.escape(key) + r"\s+([^()\s]+)\s*\)")
    while i < n:
        c = block[i]
        if c == ")":
            return None
        if c != "(":
            i += 1
            continue
        end_idx = _find_block_end(block, i)
        sub = block[i:end_idx]
        m = pat_quoted.match(sub)
        if m:
            # Un-escape backslash-escaped chars. KiCad only escapes \" and \\;
            # use a NUL sentinel so \\ is not re-processed.
            s = m.group(1)
            if "\\" in s:
                s = s.replace("\\\\", "\x00").replace('\\"', '"').replace("\x00", "\\")
            return s
        m = pat_bare.match(sub)
        if m:
            return m.group(1)
        i = end_idx
    return None


def _zone_layer(block: str) -> str | None:
    """Return the zone's layer. Handles both ``(layer "F.Cu")`` and
    ``(layers "F.Cu" "B.Cu" ...)`` forms; returns the first layer string.
    """
    val = _zone_field(block, "layer")
    if val is not None:
        return val
    # Multi-layer form: (layers "F.Cu" "B.Cu" ...).
    if not block.startswith("(zone"):
        return None
    body_start = len("(zone")
    n = len(block)
    i = body_start
    while i < n:
        c = block[i]
        if c == ")":
            return None
        if c != "(":
            i += 1
            continue
        end_idx = _find_block_end(block, i)
        sub = block[i:end_idx]
        m = re.match(r'\(layers\s+"([^"]+)"', sub)
        if m:
            return m.group(1)
        i = end_idx
    return None


def _zone_net_name(block: str) -> str | None:
    """Return the zone's net name (string), not net id."""
    return _zone_field(block, "net_name")


def _zone_uuid(block: str) -> str | None:
    return _zone_field(block, "uuid")


def _zone_name(block: str) -> str | None:
    return _zone_field(block, "name")


def _zone_polygon_points(block: str) -> list[tuple[float, float]]:
    """Extract outline polygon points from ``(polygon (pts (xy X Y) ...))``.

    Returns the points of the FIRST top-level ``(polygon ...)`` subform
    inside the zone block. Returns [] if none.
    """
    if not block.startswith("(zone"):
        return []
    body_start = len("(zone")
    n = len(block)
    i = body_start
    poly_text: str | None = None
    while i < n:
        c = block[i]
        if c == ")":
            break
        if c != "(":
            i += 1
            continue
        end_idx = _find_block_end(block, i)
        sub = block[i:end_idx]
        if sub.startswith("(polygon"):
            poly_text = sub
            break
        i = end_idx
    if poly_text is None:
        return []
    pts: list[tuple[float, float]] = []
    for m in re.finditer(r"\(xy\s+(-?[\d.]+)\s+(-?[\d.]+)\s*\)", poly_text):
        pts.append((float(m.group(1)), float(m.group(2))))
    return pts


def _locate_zone(
    text: str,
    *,
    uuid: str | None = None,
    name: str | None = None,
    net: str | None = None,
    layer: str | None = None,
) -> tuple[int, int, str]:
    """Locate a single (zone ...) block matching the selector.

    Exactly one of {uuid}, {name}, {net+layer} must be provided.
    Raises ValueError if no match. Raises ValueError with a clear message if
    multiple zones match (only possible for name / net+layer; uuid is unique).
    """
    modes = [
        ("uuid", uuid is not None),
        ("name", name is not None),
        ("net+layer", net is not None or layer is not None),
    ]
    active = [m for m, on in modes if on]
    if len(active) != 1:
        raise ValueError(
            "exactly one of {uuid}, {name}, {net+layer} must be specified"
        )
    if net is not None and layer is None:
        raise ValueError("--net requires --layer")
    if layer is not None and net is None:
        raise ValueError("--layer requires --net")

    matches: list[tuple[int, int, str]] = []
    for open_idx, end_idx, block in _iter_zone_blocks(text):
        if uuid is not None:
            if _zone_uuid(block) == uuid:
                matches.append((open_idx, end_idx, block))
        elif name is not None:
            if _zone_name(block) == name:
                matches.append((open_idx, end_idx, block))
        else:
            if _zone_net_name(block) == net and _zone_layer(block) == layer:
                matches.append((open_idx, end_idx, block))

    if not matches:
        if uuid is not None:
            raise ValueError(f"no zone with uuid {uuid!r}")
        if name is not None:
            raise ValueError(f"no zone with name {name!r}")
        raise ValueError(f"no zone with net {net!r} on layer {layer!r}")

    if len(matches) > 1:
        if name is not None:
            raise ValueError(
                f"multiple zones match name {name!r} ({len(matches)} found); "
                f"use --uuid to disambiguate"
            )
        raise ValueError(
            f"multiple zones match net {net!r} on layer {layer!r} "
            f"({len(matches)} found); use --uuid to disambiguate"
        )
    return matches[0]


def _zone_area_mm2(points: list[tuple[float, float]]) -> float:
    """Shoelace formula. Returns absolute value of the signed polygon area
    in mm^2. KiCad PCB coords are in mm.
    """
    n = len(points)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def _strip_filled_polygon(block: str) -> str:
    """Remove every top-level ``(filled_polygon ...)`` sub-block from a zone
    block. Also strips preceding whitespace/newline so we don't leave orphan
    blank lines.
    """
    if not block.startswith("(zone"):
        return block
    # Walk subforms; collect spans (including leading whitespace) to drop.
    body_start = len("(zone")
    n = len(block)
    i = body_start
    spans: list[tuple[int, int]] = []
    while i < n:
        c = block[i]
        if c == ")":
            break
        if c != "(":
            i += 1
            continue
        end_idx = _find_block_end(block, i)
        sub = block[i:end_idx]
        if sub.startswith("(filled_polygon"):
            # Extend left to swallow leading whitespace/newline.
            left = i
            while left > 0 and block[left - 1] in " \t":
                left -= 1
            if left > 0 and block[left - 1] == "\n":
                left -= 1
            spans.append((left, end_idx))
        i = end_idx
    if not spans:
        return block
    # Apply removals from the end backwards so indices remain valid.
    out = block
    for start, end in reversed(spans):
        out = out[:start] + out[end:]
    return out


def _resolve_net_id(text: str, net_name: str) -> int | None:
    """Scan top-level ``(net N "name")`` entries; return the id matching
    ``net_name``. None if not found.
    """
    for open_idx, end_idx in _iter_top_blocks(text, "net"):
        sub = text[open_idx:end_idx]
        m = re.match(r'\(net\s+(\d+)\s+"((?:[^"\\]|\\.)*)"\s*\)', sub)
        if not m:
            # Net 0 may be emitted as (net 0 "") which still matches above.
            continue
        if m.group(2) == net_name:
            return int(m.group(1))
    return None


# Top-level zone subform heads that should NOT be carried over when copying
# settings to a new zone (these are per-zone identity / geometry).
_ZONE_IDENTITY_HEADS = frozenset(
    {"uuid", "name", "net", "net_name", "layer", "layers", "polygon", "filled_polygon"}
)


def _zone_settings_for_copy(block: str) -> str:
    """Return a textual fragment of the zone block's "settings tail" — i.e.
    every top-level subform EXCEPT identity / geometry forms (uuid, name,
    net, net_name, layer/layers, polygon, filled_polygon).

    The fragment preserves the original whitespace/newlines between the
    retained subforms so it can be spliced into a freshly built zone block.
    Leading whitespace before the first kept subform is included; trailing
    whitespace up to (but not including) the closing ')' is preserved.
    """
    if not block.startswith("(zone"):
        return ""
    body_start = len("(zone")
    n = len(block)
    i = body_start
    kept: list[str] = []
    while i < n:
        c = block[i]
        if c == ")":
            break
        if c != "(":
            i += 1
            continue
        end_idx = _find_block_end(block, i)
        sub = block[i:end_idx]
        m = re.match(r"\(([A-Za-z_][A-Za-z0-9_]*)", sub)
        head = m.group(1) if m else ""
        if head not in _ZONE_IDENTITY_HEADS:
            # Capture leading whitespace (newline + tabs) before this subform.
            left = i
            while left > 0 and block[left - 1] in " \t":
                left -= 1
            if left > 0 and block[left - 1] == "\n":
                left -= 1
            kept.append(block[left:end_idx])
        i = end_idx
    return "".join(kept)


def _build_zone_block(
    *,
    uuid: str,
    name: str | None,
    net_id: int,
    net_name: str,
    layer: str,
    priority: int | None,
    settings_tail: str,
    polygon_points: list[tuple[float, float]],
) -> str:
    """Build a fresh ``(zone ...)`` block as a string.

    Indentation matches KiCad's emitted format (tab indent, body lines at
    depth-2 = two tabs). The block itself has no leading tab; callers are
    expected to splice it in at depth 1 with the surrounding newline+tab.

    If ``settings_tail`` is non-empty, it is spliced in before the polygon
    (matches KiCad's canonical zone layout: settings precede geometry).
    If ``priority`` is None, the ``(priority ...)`` line is omitted.
    If ``name`` is None, the ``(name ...)`` line is omitted.
    """
    lines: list[str] = []
    lines.append("(zone")
    lines.append(f'\t\t(net {net_id})')
    lines.append(f'\t\t(net_name "{net_name}")')
    lines.append(f'\t\t(layer "{layer}")')
    lines.append(f'\t\t(uuid "{uuid}")')
    if name is not None:
        # Escape embedded quotes.
        esc = name.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'\t\t(name "{esc}")')
    if priority is not None:
        lines.append(f"\t\t(priority {int(priority)})")

    head = "\n".join(lines)
    # Settings tail (already contains its own leading newline + indent per
    # kept subform). Splice it in BEFORE the polygon so the resulting block
    # matches KiCad's canonical layout (hatch / connect_pads / min_thickness
    # / fill all precede the polygon outline).

    # Polygon (outline) — pts on a single line, matching KiCad's emitted
    # canonical form. Avoids re-format churn after the next KiCad save.
    xy_tokens = " ".join(
        f"(xy {_fmt_coord(x)} {_fmt_coord(y)})" for x, y in polygon_points
    )
    poly_block = "\n".join(
        [
            "\t\t(polygon",
            f"\t\t\t(pts {xy_tokens})",
            "\t\t)",
        ]
    )

    return head + settings_tail + "\n" + poly_block + "\n\t)"


# ---------------------------------------------------------------------------
# Zone edit operations
# ---------------------------------------------------------------------------


def _zone_selector_args(
    *, uuid: str | None, name: str | None, net: str | None, layer: str | None
) -> dict[str, str | None]:
    """Normalize the selector kwargs for ``_locate_zone``."""
    return {"uuid": uuid, "name": name, "net": net, "layer": layer}


def _splice_zone_block(text: str, open_idx: int, end_idx: int, new_block: str) -> str:
    """Replace text[open_idx:end_idx] (a zone block) with new_block."""
    return text[:open_idx] + new_block + text[end_idx:]


def _replace_polygon_pts(block: str, points: list[tuple[float, float]]) -> str:
    """Replace the first top-level ``(polygon (pts ...))`` inside a zone block
    with a new polygon containing ``points``. Preserves indentation."""
    if not block.startswith("(zone"):
        raise ValueError("not a zone block")
    body_start = len("(zone")
    n = len(block)
    i = body_start
    while i < n:
        c = block[i]
        if c == ")":
            break
        if c != "(":
            i += 1
            continue
        end_idx = _find_block_end(block, i)
        sub = block[i:end_idx]
        if sub.startswith("(polygon"):
            # Determine indent (whitespace at start of this line).
            line_start = block.rfind("\n", 0, i) + 1
            indent = block[line_start:i]
            body_indent = indent + "\t"
            # Emit pts on a single line, matching KiCad's canonical form.
            xy_tokens = " ".join(
                f"(xy {_fmt_coord(x)} {_fmt_coord(y)})" for x, y in points
            )
            new_poly = "\n".join(
                [
                    "(polygon",
                    f"{body_indent}(pts {xy_tokens})",
                    f"{indent})",
                ]
            )
            return block[:i] + new_poly + block[end_idx:]
        i = end_idx
    raise ValueError("zone has no (polygon ...) outline subform")


def _check_polygon_valid(points: list[tuple[float, float]]) -> dict[str, Any]:
    """Validate a polygon outline. Returns details (area_mm2, warnings).

    Raises ``ValueError`` on degenerate input (<3 points, area 0, or
    consecutive duplicate points). Self-intersection is reported as a
    warning entry, not an error.
    """
    if len(points) < 3:
        raise ValueError(f"polygon needs at least 3 points, got {len(points)}")
    for i in range(len(points)):
        if points[i] == points[(i + 1) % len(points)]:
            raise ValueError(
                f"polygon has consecutive duplicate point at index {i}: {points[i]}"
            )
    area = _zone_area_mm2(points)
    if area == 0.0:
        raise ValueError("polygon has zero area (collinear / degenerate)")
    warnings: list[str] = []
    if _polygon_self_intersects(points):
        msg = "polygon outline appears to self-intersect"
        sys.stderr.write(f"WARNING: {msg}\n")
        warnings.append(msg)
    return {"area_mm2": area, "warnings": warnings}


def _segments_cross(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    """Proper segment-crossing test (shared endpoints don't count)."""
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    if a1 == b1 or a1 == b2 or a2 == b1 or a2 == b2:
        return False
    d1 = cross(b1, b2, a1)
    d2 = cross(b1, b2, a2)
    d3 = cross(a1, a2, b1)
    d4 = cross(a1, a2, b2)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    ):
        return True
    return False


def _polygon_self_intersects(points: list[tuple[float, float]]) -> bool:
    n = len(points)
    if n < 4:
        return False
    edges = [(points[i], points[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            # skip adjacent edges (share a vertex)
            if j == i + 1 or (i == 0 and j == n - 1):
                continue
            if _segments_cross(edges[i][0], edges[i][1], edges[j][0], edges[j][1]):
                return True
    return False


def set_zone_polygon(
    pcb_path: str | Path,
    *,
    uuid: str | None = None,
    name: str | None = None,
    net: str | None = None,
    layer: str | None = None,
    points: list[tuple[float, float]],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Replace a zone's polygon outline points.

    Strips all stale ``(filled_polygon ...)`` sub-blocks after the edit; refill
    is expected to be handled downstream (KiCad CLI / DRC). The zone's uuid,
    name, net, layer, settings, and priority are preserved.
    """
    pcb_path = Path(pcb_path)
    text = _read(pcb_path)

    open_idx, end_idx, block = _locate_zone(
        text, **_zone_selector_args(uuid=uuid, name=name, net=net, layer=layer)
    )

    info = _check_polygon_valid(points)

    new_block = _replace_polygon_pts(block, points)
    new_block = _strip_filled_polygon(new_block)
    new_text = _splice_zone_block(text, open_idx, end_idx, new_block)

    changed = _maybe_write(pcb_path, text, new_text, dry_run)
    return {
        "action": "set_zone_polygon",
        "changed": changed,
        "diff": _diff(text, new_text, pcb_path),
        "details": {
            "uuid": _zone_uuid(new_block),
            "name": _zone_name(new_block),
            "layer": _zone_layer(new_block),
            "points": [[x, y] for (x, y) in points],
            "area_mm2": info["area_mm2"],
            "warnings": info["warnings"],
        },
    }


def _validate_layer(text: str, layer: str) -> None:
    """Ensure ``layer`` appears as a defined layer in the board's
    ``(layers ...)`` block. Raises ``ValueError`` if unknown."""
    m = re.search(r"^\t\(layers\b", text, re.MULTILINE)
    if not m:
        raise ValueError("board has no (layers ...) declaration")
    open_idx = m.start() + 1
    end_idx = _find_block_end(text, open_idx)
    layers_text = text[open_idx:end_idx]
    names = re.findall(r'"([^"]+)"', layers_text)
    if layer not in names:
        raise ValueError(
            f"unknown layer {layer!r}; defined layers: {', '.join(names)}"
        )


def _find_last_top_block_end(text: str, token: str) -> int | None:
    last_end: int | None = None
    for _open_idx, end_idx in _iter_top_blocks(text, token):
        last_end = end_idx
    return last_end


def _kicad_pcb_outer_close(text: str) -> int:
    """Index of the closing ``)`` of the outer ``(kicad_pcb ...)`` form."""
    m = re.search(r"\(kicad_pcb\b", text)
    if not m:
        raise ValueError("not a kicad_pcb file (no (kicad_pcb ...) header)")
    end_idx = _find_block_end(text, m.start())
    return end_idx - 1  # position of the final ')'


def add_zone(
    pcb_path: str | Path,
    net: str,
    layer: str,
    points: list[tuple[float, float]],
    *,
    copy_settings_from_uuid: str | None = None,
    name: str | None = None,
    priority: int | None = None,
    clearance: float | None = None,
    min_thickness: float | None = None,
    thermal_gap: float | None = None,
    thermal_bridge_width: float | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Add a new ``(zone ...)`` block to the board.

    Two settings sources:

    - ``copy_settings_from_uuid`` (primary): copy every non-identity subform
      from the named source zone (clearance, fill, hatch, connect_pads, ...).
    - explicit flags (secondary): build a minimal default settings tail with
      the provided values. Requires at least one of ``clearance``, ``min_thickness``,
      ``thermal_gap``, ``thermal_bridge_width`` to be supplied so the caller
      cannot accidentally create a zone with no settings.
    """
    pcb_path = Path(pcb_path)
    text = _read(pcb_path)

    info = _check_polygon_valid(points)
    _validate_layer(text, layer)

    net_id = _resolve_net_id(text, net)
    if net_id is None:
        raise ValueError(f"unknown net {net!r}; add it to the netlist first")

    source_uuid: str | None = None
    settings_tail: str = ""
    if copy_settings_from_uuid is not None:
        _o, _e, src_block = _locate_zone(text, uuid=copy_settings_from_uuid)
        settings_tail = _zone_settings_for_copy(src_block)
        source_uuid = copy_settings_from_uuid
        # Always strip any inherited (priority ...) from the copied tail so
        # _build_zone_block's explicit ``priority`` arg is the single source
        # of truth. If the caller did not pass --priority, fall back to the
        # source zone's priority value.
        inherited_priority_match = re.search(
            r"\n[\t ]*\(priority\s+(-?\d+)\s*\)",
            settings_tail,
        )
        settings_tail = re.sub(
            r"\n[\t ]*\(priority\s+-?\d+\s*\)",
            "",
            settings_tail,
        )
        if priority is None and inherited_priority_match is not None:
            priority = int(inherited_priority_match.group(1))
    else:
        explicit_any = any(
            v is not None
            for v in (clearance, min_thickness, thermal_gap, thermal_bridge_width)
        )
        if not explicit_any:
            raise ValueError(
                "either --copy-settings-from-uuid or at least one explicit setting "
                "(--clearance / --min-thickness / --thermal-gap / --thermal-bridge-width) "
                "must be provided"
            )
        # Build a minimal default settings tail in the same indent style as
        # _build_zone_block emits (two-tab body).
        tail_lines: list[str] = []
        tail_lines.append("\t\t(hatch edge 0.5)")
        clr = clearance if clearance is not None else 0.5
        mt = min_thickness if min_thickness is not None else 0.25
        tg = thermal_gap if thermal_gap is not None else 0.5
        tbw = thermal_bridge_width if thermal_bridge_width is not None else 0.5
        tail_lines.append("\t\t(connect_pads yes")
        tail_lines.append(f"\t\t\t(clearance {_fmt_coord(clr)})")
        tail_lines.append("\t\t)")
        tail_lines.append(f"\t\t(min_thickness {_fmt_coord(mt)})")
        tail_lines.append("\t\t(fill yes")
        tail_lines.append(f"\t\t\t(thermal_gap {_fmt_coord(tg)})")
        tail_lines.append(f"\t\t\t(thermal_bridge_width {_fmt_coord(tbw)})")
        tail_lines.append("\t\t\t(island_removal_mode 0)")
        tail_lines.append("\t\t)")
        # _build_zone_block puts settings_tail BEFORE the closing of the zone;
        # each kept line needs its own leading newline (matches _zone_settings_for_copy).
        settings_tail = "".join("\n" + ln for ln in tail_lines)

    new_uuid = _det_uuid(f"zone:{net}:{layer}:{points!r}")

    new_block_body = _build_zone_block(
        uuid=new_uuid,
        name=name,
        net_id=net_id,
        net_name=net,
        layer=layer,
        priority=priority,
        settings_tail=settings_tail,
        polygon_points=points,
    )

    # Insert: prefer right after the last existing zone (depth-1, single tab
    # leading indent). If no zones exist, insert just before the closing ')'
    # of the (kicad_pcb ...) form.
    last_zone_end = _find_last_top_block_end(text, "zone")
    if last_zone_end is not None:
        # Insert "\n\t<new_block_body>" right after the last zone, then the
        # existing newline after the previous zone (if any) is preserved.
        # _build_zone_block returns "(zone\n\t\t...\n\t)" — depth-1 indent
        # supplied by us.
        insertion = "\n\t" + new_block_body
        # last_zone_end points just past ')' of last zone — usually followed by '\n'.
        new_text = text[:last_zone_end] + insertion + text[last_zone_end:]
    else:
        close_idx = _kicad_pcb_outer_close(text)
        insertion = "\t" + new_block_body + "\n"
        new_text = text[:close_idx] + insertion + text[close_idx:]

    changed = _maybe_write(pcb_path, text, new_text, dry_run)
    details: dict[str, Any] = {
        "uuid": new_uuid,
        "name": name,
        "layer": layer,
        "net": net,
        "area_mm2": info["area_mm2"],
        "warnings": info["warnings"],
    }
    if source_uuid is not None:
        details["source_uuid"] = source_uuid
    return {
        "action": "add_zone",
        "changed": changed,
        "diff": _diff(text, new_text, pcb_path),
        "details": details,
    }


def delete_zone(
    pcb_path: str | Path,
    *,
    uuid: str | None = None,
    name: str | None = None,
    net: str | None = None,
    layer: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a single ``(zone ...)`` block. Tracks/vias/other zones are
    untouched."""
    pcb_path = Path(pcb_path)
    text = _read(pcb_path)

    open_idx, end_idx, block = _locate_zone(
        text, **_zone_selector_args(uuid=uuid, name=name, net=net, layer=layer)
    )

    details = {
        "uuid": _zone_uuid(block),
        "name": _zone_name(block),
        "layer": _zone_layer(block),
        "net": _zone_net_name(block),
    }

    # Drop the leading tab+newline so we don't leave an orphan blank line.
    line_start = text.rfind("\n", 0, open_idx) + 1
    leading_ws = text[line_start:open_idx]
    cut_start = open_idx - len(leading_ws) if leading_ws.strip() == "" else open_idx
    cut_end = end_idx
    if cut_end < len(text) and text[cut_end] == "\n":
        cut_end += 1

    new_text = text[:cut_start] + text[cut_end:]

    changed = _maybe_write(pcb_path, text, new_text, dry_run)
    return {
        "action": "delete_zone",
        "changed": changed,
        "diff": _diff(text, new_text, pcb_path),
        "details": details,
    }


_ZONE_PROPERTY_KEYS = (
    "priority",
    "clearance",
    "min_thickness",
    "thermal_gap",
    "thermal_bridge_width",
    "name",
)


def _set_top_zone_field(block: str, key: str, formatted_value: str) -> tuple[str, str | None]:
    """Replace the value of a single top-level ``(key ...)`` subform in a zone
    block. Returns (new_block, old_value_string_or_None). Raises ValueError
    if the subform is absent."""
    if not block.startswith("(zone"):
        raise ValueError("not a zone block")
    body_start = len("(zone")
    n = len(block)
    i = body_start
    while i < n:
        c = block[i]
        if c == ")":
            break
        if c != "(":
            i += 1
            continue
        end_idx = _find_block_end(block, i)
        sub = block[i:end_idx]
        m = re.match(r"\(([A-Za-z_][A-Za-z0-9_]*)", sub)
        if m and m.group(1) == key:
            old = sub
            new_sub = f"({key} {formatted_value})"
            return block[:i] + new_sub + block[end_idx:], old
        i = end_idx
    raise ValueError(f"zone has no top-level ({key} ...) subform")


def _set_nested_zone_field(
    block: str, parent_key: str, key: str, formatted_value: str
) -> tuple[str, str | None]:
    """Replace the value of a ``(key ...)`` subform nested inside a top-level
    ``(parent_key ...)`` subform of a zone block."""
    if not block.startswith("(zone"):
        raise ValueError("not a zone block")
    body_start = len("(zone")
    n = len(block)
    i = body_start
    while i < n:
        c = block[i]
        if c == ")":
            break
        if c != "(":
            i += 1
            continue
        end_idx = _find_block_end(block, i)
        sub = block[i:end_idx]
        m = re.match(r"\(([A-Za-z_][A-Za-z0-9_]*)", sub)
        if m and m.group(1) == parent_key:
            inner_pat = re.compile(
                r"\(" + re.escape(key) + r"\s+([^()\s]+)\s*\)"
            )
            inner_m = inner_pat.search(sub)
            if not inner_m:
                raise ValueError(
                    f"zone ({parent_key} ...) has no inner ({key} ...) field"
                )
            old = inner_m.group(1)
            new_sub = sub[: inner_m.start()] + f"({key} {formatted_value})" + sub[inner_m.end():]
            return block[:i] + new_sub + block[end_idx:], old
        i = end_idx
    raise ValueError(f"zone has no top-level ({parent_key} ...) subform")


def set_zone_property(
    pcb_path: str | Path,
    *,
    uuid: str | None = None,
    name: str | None = None,
    key: str,
    value: Any,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Set a single scalar property on a zone. Settings changes invalidate
    any existing fill, so ``(filled_polygon ...)`` blocks are stripped."""
    if key not in _ZONE_PROPERTY_KEYS:
        raise ValueError(
            f"unsupported zone property {key!r}; choose from {_ZONE_PROPERTY_KEYS}"
        )
    # net+layer selector is intentionally not supported here; the plan
    # restricts this command to uuid/name.
    if (uuid is None) == (name is None):
        raise ValueError("exactly one of {uuid, name} must be specified")

    pcb_path = Path(pcb_path)
    text = _read(pcb_path)

    open_idx, end_idx, block = _locate_zone(text, uuid=uuid, name=name)

    new_block: str
    old_value: str | None
    if key == "name":
        new_value = str(value)
        esc = new_value.replace("\\", "\\\\").replace('"', '\\"')
        new_block, old_value = _set_top_zone_field(block, "name", f'"{esc}"')
        # Strip surrounding quotes for the reported old value.
        if old_value:
            mq = re.match(r'\(name\s+"((?:[^"\\]|\\.)*)"\s*\)', old_value)
            old_value = mq.group(1) if mq else old_value
    elif key == "priority":
        ivalue = int(value)
        new_block, old_value = _set_top_zone_field(block, "priority", str(ivalue))
        if old_value:
            mp = re.match(r"\(priority\s+(-?\d+)\s*\)", old_value)
            old_value = mp.group(1) if mp else old_value
    elif key == "min_thickness":
        fvalue = float(value)
        new_block, old_value = _set_top_zone_field(
            block, "min_thickness", _fmt_coord(fvalue)
        )
        if old_value:
            mt = re.match(r"\(min_thickness\s+(-?[\d.]+)\s*\)", old_value)
            old_value = mt.group(1) if mt else old_value
    elif key == "clearance":
        fvalue = float(value)
        new_block, old_value = _set_nested_zone_field(
            block, "connect_pads", "clearance", _fmt_coord(fvalue)
        )
    elif key == "thermal_gap":
        fvalue = float(value)
        new_block, old_value = _set_nested_zone_field(
            block, "fill", "thermal_gap", _fmt_coord(fvalue)
        )
    elif key == "thermal_bridge_width":
        fvalue = float(value)
        new_block, old_value = _set_nested_zone_field(
            block, "fill", "thermal_bridge_width", _fmt_coord(fvalue)
        )
    else:  # pragma: no cover — guarded by whitelist
        raise ValueError(f"unsupported key {key!r}")

    new_block = _strip_filled_polygon(new_block)
    new_text = _splice_zone_block(text, open_idx, end_idx, new_block)

    changed = _maybe_write(pcb_path, text, new_text, dry_run)
    return {
        "action": "set_zone_property",
        "changed": changed,
        "diff": _diff(text, new_text, pcb_path),
        "details": {
            "uuid": _zone_uuid(new_block),
            "key": key,
            "old": old_value,
            "new": value,
        },
    }


__all__ = [
    "move_footprint",
    "move_footprint_property",
    "move_footprint_layer",
    "delete_footprint",
    "import_footprints",
    "sync_from_schematic",
    "set_zone_polygon",
    "add_zone",
    "delete_zone",
    "set_zone_property",
]
