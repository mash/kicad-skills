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
            "set Reference via the schematic + 'pcb import-footprints' instead "
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
#   * re-running ``import-footprints`` is a no-op (same refs hash to the same
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
    new_at = f'\t(at {_fmt_coord(at_xy[0])} {_fmt_coord(at_xy[1])} 0)\n'
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

    # 8. Re-indent.
    text = _reindent_footprint_block(text)

    # Ensure the block ends with a single trailing newline.
    if not text.endswith("\n"):
        text += "\n"
    return text


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
    # Local import keeps pcb_edit usable without the netlist module on read
    # paths that don't need import functionality.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pcb_netlist  # noqa: WPS433

    pcb_path = Path(pcb_path)
    schematic_netlist_path = Path(schematic_netlist_path)
    project_dir = Path(project_dir) if project_dir is not None else pcb_path.parent

    text = _read(pcb_path)
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
        new_blocks.append(block)
        added.append({
            "ref": ref,
            "lib_id": lib_id,
            "value": comp.get("value", ""),
            "at": {"x": xy[0], "y": xy[1]},
            "source": str(mod_path),
        })

    # Insertion: just before the final `)` (the close of `(kicad_pcb ...)`).
    if new_blocks:
        # Find the final closing paren of the file.
        m = _re.search(r"\)\s*\Z", text)
        if not m:
            raise ValueError("could not locate closing paren of kicad_pcb form")
        insert_at = m.start()
        new_text = text[:insert_at] + "".join(new_blocks) + text[insert_at:]
    else:
        new_text = text

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

    # Parity check (post-import view): compare schematic refs vs. resulting
    # footprint refs on the target board.
    after_text = new_text
    after_refs = _existing_refs(after_text)
    parity_missing = sorted(set(sch_refs) - after_refs, key=_ref_sort_key)
    parity_extra = sorted(after_refs - set(sch_refs), key=_ref_sort_key)
    parity_clean = not parity_missing and not unresolved

    return {
        "action": "import_footprints",
        "changed": text != new_text,
        "wrote": wrote,
        "target": str(target),
        "input_board": str(pcb_path),
        "diff": diff,
        "details": {
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
        },
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


__all__ = [
    "move_footprint",
    "move_footprint_property",
    "move_footprint_layer",
    "delete_footprint",
    "import_footprints",
]
