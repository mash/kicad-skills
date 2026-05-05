"""Read-only PCB queries behind ``pcb query <element>``.

Mirrors ``sch_query.py`` style: each ``query_*`` returns plain dict/list
suitable for ``json.dumps``. Never mutates the file.

Subcommand mapping (see plan ``202605021336-kicad-pcb-cli-tool.md``):

    pcb query list <element>       -> query_list
    pcb query footprint <REF>      -> query_footprint
    pcb query pad <REF.PAD>        -> query_pad
    pcb query net <NAME|REF.PAD>   -> query_net
    pcb query region <X1,Y1,X2,Y2> -> query_region
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from kiutils.board import Board

from text_collision_core import Rect


# ---------------------------------------------------------------------------
# Loading & shared helpers
# ---------------------------------------------------------------------------


def load_board(path: str | Path) -> Board:
    return Board().from_file(str(path))


def _as_board(b: Board | str | Path) -> Board:
    if isinstance(b, Board):
        return b
    return load_board(b)


def _ref_of(fp) -> str | None:
    # KiCad v8+ stores reference in the properties dict; older files use an
    # FpText with type=="reference" in graphicItems.
    props = getattr(fp, "properties", None) or {}
    if isinstance(props, dict) and "Reference" in props:
        return props["Reference"]
    for gi in getattr(fp, "graphicItems", []) or []:
        if getattr(gi, "type", None) == "reference":
            return getattr(gi, "text", None)
    return None


def _value_of(fp) -> str | None:
    props = getattr(fp, "properties", None) or {}
    if isinstance(props, dict) and "Value" in props:
        return props["Value"]
    for gi in getattr(fp, "graphicItems", []) or []:
        if getattr(gi, "type", None) == "value":
            return getattr(gi, "text", None)
    return None


def _pos_dict(pos) -> dict[str, Any]:
    if pos is None:
        return {"x": None, "y": None, "angle": 0}
    return {
        "x": pos.X,
        "y": pos.Y,
        "angle": float(pos.angle or 0),
    }


def _rotate(x: float, y: float, angle_deg: float) -> tuple[float, float]:
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    return (c * x - s * y, s * x + c * y)


def _fp_to_abs(fp, lx: float, ly: float) -> tuple[float, float]:
    if fp.position is None:
        return (lx, ly)
    rot = float(fp.position.angle or 0)
    # KiCad mirrors back-side footprint geometry across the X axis: local
    # pad/graphic Y is negated before rotation/translation when fp.layer is
    # a back layer (e.g. "B.Cu"). Front layers are unchanged.
    layer = getattr(fp, "layer", None) or ""
    if layer.startswith("B."):
        ly = -ly
    rx, ry = _rotate(lx, ly, rot)
    # KiCad stores PCB Y-down; rotation in kiutils-stored angle matches the
    # native footprint orientation, so a simple translate after rotation is
    # the correct absolute pad position.
    return (fp.position.X + rx, fp.position.Y + ry)


def _net_dict(net) -> dict[str, Any] | None:
    if net is None:
        return None
    return {"number": getattr(net, "number", None), "name": getattr(net, "name", None)}


# ---------------------------------------------------------------------------
# Footprint helpers
# ---------------------------------------------------------------------------


def _find_footprint(board: Board, ref: str):
    for fp in board.footprints:
        if _ref_of(fp) == ref:
            return fp
    return None


def _pad_summary(fp) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pad in fp.pads:
        ax, ay = _fp_to_abs(fp, pad.position.X, pad.position.Y)
        out.append({
            "number": pad.number,
            "type": pad.type,
            "shape": pad.shape,
            "absolute": {"x": ax, "y": ay},
            "size": {"x": pad.size.X if pad.size else None, "y": pad.size.Y if pad.size else None},
            "layers": list(getattr(pad, "layers", []) or []),
            "net": _net_dict(getattr(pad, "net", None)),
        })
    return out


def _fp_bbox(fp) -> tuple[float, float, float, float] | None:
    """Approximate footprint bbox using pad absolute centers (+ pad size)."""
    if fp.position is None:
        return None
    pts: list[tuple[float, float]] = []
    if not fp.pads:
        return (fp.position.X, fp.position.Y, fp.position.X, fp.position.Y)
    for pad in fp.pads:
        ax, ay = _fp_to_abs(fp, pad.position.X, pad.position.Y)
        sx = pad.size.X / 2.0 if pad.size else 0.0
        sy = pad.size.Y / 2.0 if pad.size else 0.0
        pts.append((ax - sx, ay - sy))
        pts.append((ax + sx, ay + sy))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------------------
# query_footprint
# ---------------------------------------------------------------------------


def query_footprint(board: Board | str | Path, ref: str) -> dict[str, Any]:
    b = _as_board(board)
    fp = _find_footprint(b, ref)
    if fp is None:
        return {"found": False, "ref": ref}
    props = dict(getattr(fp, "properties", {}) or {})
    return {
        "found": True,
        "ref": ref,
        "lib_id": fp.libId,
        "value": _value_of(fp),
        "layer": fp.layer,
        "at": _pos_dict(fp.position),
        "rotation": float(fp.position.angle or 0) if fp.position else 0.0,
        "locked": bool(fp.locked),
        "placed": bool(fp.placed),
        "tstamp": fp.tstamp,
        "properties": props,
        "pads": _pad_summary(fp),
    }


# ---------------------------------------------------------------------------
# query_pad
# ---------------------------------------------------------------------------


def _split_ref_pad(ref_pad: str) -> tuple[str, str]:
    if "." not in ref_pad:
        raise ValueError(f"expected REF.PAD, got {ref_pad!r}")
    ref, pad = ref_pad.split(".", 1)
    return ref, pad


def query_pad(board: Board | str | Path, ref_pad: str) -> dict[str, Any]:
    b = _as_board(board)
    ref, pad_num = _split_ref_pad(ref_pad)
    fp = _find_footprint(b, ref)
    if fp is None:
        return {"found": False, "ref": ref, "pad": pad_num, "reason": "footprint not found"}
    pad = next((p for p in fp.pads if p.number == pad_num), None)
    if pad is None:
        return {"found": False, "ref": ref, "pad": pad_num, "reason": "pad not found"}
    ax, ay = _fp_to_abs(fp, pad.position.X, pad.position.Y)
    return {
        "found": True,
        "ref": ref,
        "pad": pad_num,
        "type": pad.type,
        "shape": pad.shape,
        "absolute": {"x": ax, "y": ay},
        "local": {"x": pad.position.X, "y": pad.position.Y, "angle": float(pad.position.angle or 0)},
        "footprint_at": _pos_dict(fp.position),
        "size": {"x": pad.size.X if pad.size else None, "y": pad.size.Y if pad.size else None},
        "drill": {
            "diameter": getattr(pad.drill, "diameter", None) if pad.drill else None,
            "offset": (
                {"x": pad.drill.offset[0].X, "y": pad.drill.offset[0].Y}
                if pad.drill and getattr(pad.drill, "offset", None)
                else None
            ),
        } if pad.drill else None,
        "layers": list(getattr(pad, "layers", []) or []),
        "net": _net_dict(getattr(pad, "net", None)),
        "pinFunction": getattr(pad, "pinFunction", None),
        "pinType": getattr(pad, "pinType", None),
    }


# ---------------------------------------------------------------------------
# query_net
# ---------------------------------------------------------------------------


def _pad_has_ref(board: Board, ref: str, pad_num: str) -> bool:
    fp = _find_footprint(board, ref)
    if fp is None:
        return False
    return any(p.number == pad_num for p in fp.pads)


def _net_members(board: Board, net_name: str) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    for fp in board.footprints:
        ref = _ref_of(fp)
        for pad in fp.pads:
            net = getattr(pad, "net", None)
            if net is not None and getattr(net, "name", None) == net_name:
                ax, ay = _fp_to_abs(fp, pad.position.X, pad.position.Y)
                members.append({
                    "ref": ref,
                    "pad": pad.number,
                    "absolute": {"x": ax, "y": ay},
                    "layers": list(getattr(pad, "layers", []) or []),
                })
    return members


def _track_segments_on_net(board: Board, net_name: str) -> dict[str, list[dict[str, Any]]]:
    segs: list[dict[str, Any]] = []
    vias: list[dict[str, Any]] = []
    for item in board.traceItems:
        net = getattr(item, "net", None)
        # net here is a number (int), not a Net object — we resolve via board.nets
        if net is None:
            continue
        cls = type(item).__name__
        if cls == "Via":
            # Via has position
            if _net_number_to_name(board, net) == net_name:
                vias.append({
                    "at": {"x": item.position.X, "y": item.position.Y},
                    "size": item.size,
                    "drill": item.drill,
                    "layers": list(getattr(item, "layers", []) or []),
                    "tstamp": item.tstamp,
                })
        else:
            if _net_number_to_name(board, net) == net_name:
                segs.append({
                    "kind": cls.lower(),
                    "start": {"x": item.start.X, "y": item.start.Y} if hasattr(item, "start") else None,
                    "end": {"x": item.end.X, "y": item.end.Y} if hasattr(item, "end") else None,
                    "width": item.width,
                    "layer": item.layer,
                    "tstamp": item.tstamp,
                })
    return {"segments": segs, "vias": vias}


def _net_number_to_name(board: Board, num: int) -> str | None:
    for n in board.nets:
        if n.number == num:
            return n.name
    return None


def query_net(board: Board | str | Path, target: str) -> dict[str, Any]:
    """Resolve `target` as REF.PAD only if both ref and pad exist on the board.
    Otherwise treat the entire string as a net name."""
    b = _as_board(board)
    name: str | None = None
    if "." in target:
        ref, pad_num = target.split(".", 1)
        if _pad_has_ref(b, ref, pad_num):
            fp = _find_footprint(b, ref)
            pad = next(p for p in fp.pads if p.number == pad_num)
            net = getattr(pad, "net", None)
            name = getattr(net, "name", None) if net is not None else None
            if name is None:
                return {
                    "found": False,
                    "ref": ref,
                    "pad": pad_num,
                    "reason": "pad has no net",
                }
        else:
            name = target
    else:
        name = target

    members = _net_members(b, name)
    tracks = _track_segments_on_net(b, name)
    found = bool(members) or bool(tracks["segments"]) or bool(tracks["vias"])
    return {
        "found": found,
        "name": name,
        "members": members,
        "segments": tracks["segments"],
        "vias": tracks["vias"],
    }


# ---------------------------------------------------------------------------
# query_region
# ---------------------------------------------------------------------------


def _segment_intersects(rect: Rect, p1: tuple[float, float], p2: tuple[float, float]) -> bool:
    return rect.intersects_segment(p1, p2)


def _drawing_bbox(item) -> tuple[float, float, float, float] | None:
    cls = type(item).__name__
    if cls in ("GrLine", "GrArc"):
        return (item.start.X, item.start.Y, item.end.X, item.end.Y)
    if cls == "GrRect":
        return (item.start.X, item.start.Y, item.end.X, item.end.Y)
    if cls == "GrCircle":
        cx, cy = item.center.X, item.center.Y
        ex, ey = item.end.X, item.end.Y
        r = ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5
        return (cx - r, cy - r, cx + r, cy + r)
    if cls == "GrText":
        x, y = item.position.X, item.position.Y
        return (x - 1.0, y - 1.0, x + 1.0, y + 1.0)
    if cls == "GrPoly":
        pts = [(p.X, p.Y) for p in (item.coordinates or [])]
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))
    return None


def _zone_bbox(zone) -> tuple[float, float, float, float] | None:
    pts: list[tuple[float, float]] = []
    for poly in getattr(zone, "polygons", []) or []:
        for p in getattr(poly, "coordinates", []) or []:
            pts.append((p.X, p.Y))
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def query_region(board: Board | str | Path, bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    b = _as_board(board)
    rect = Rect(bbox[0], bbox[1], bbox[2], bbox[3]).normalized()

    fp_hits: list[dict[str, Any]] = []
    for fp in b.footprints:
        bb = _fp_bbox(fp)
        if bb is None:
            continue
        fb = Rect(bb[0], bb[1], bb[2], bb[3]).normalized()
        if rect.intersects_rect(fb):
            fp_hits.append({
                "ref": _ref_of(fp),
                "lib_id": fp.libId,
                "layer": fp.layer,
                "at": _pos_dict(fp.position),
                "bbox": {"x1": fb.x1, "y1": fb.y1, "x2": fb.x2, "y2": fb.y2},
            })

    drawing_hits: list[dict[str, Any]] = []
    for item in b.graphicItems:
        bb = _drawing_bbox(item)
        if bb is None:
            continue
        gb = Rect(bb[0], bb[1], bb[2], bb[3]).normalized()
        if rect.intersects_rect(gb):
            drawing_hits.append({
                "kind": type(item).__name__,
                "layer": getattr(item, "layer", None),
                "bbox": {"x1": gb.x1, "y1": gb.y1, "x2": gb.x2, "y2": gb.y2},
                "tstamp": getattr(item, "tstamp", None),
            })

    track_hits: list[dict[str, Any]] = []
    via_hits: list[dict[str, Any]] = []
    for item in b.traceItems:
        cls = type(item).__name__
        if cls == "Via":
            x, y = item.position.X, item.position.Y
            if rect.x1 <= x <= rect.x2 and rect.y1 <= y <= rect.y2:
                via_hits.append({
                    "at": {"x": x, "y": y},
                    "size": item.size,
                    "drill": item.drill,
                    "net": item.net,
                    "tstamp": item.tstamp,
                })
        else:
            if hasattr(item, "start") and hasattr(item, "end"):
                p1 = (item.start.X, item.start.Y)
                p2 = (item.end.X, item.end.Y)
                if _segment_intersects(rect, p1, p2):
                    track_hits.append({
                        "kind": cls.lower(),
                        "start": {"x": p1[0], "y": p1[1]},
                        "end": {"x": p2[0], "y": p2[1]},
                        "width": item.width,
                        "layer": item.layer,
                        "net": item.net,
                        "tstamp": item.tstamp,
                    })

    zone_hits: list[dict[str, Any]] = []
    for zone in b.zones:
        bb = _zone_bbox(zone)
        if bb is None:
            continue
        zb = Rect(bb[0], bb[1], bb[2], bb[3]).normalized()
        if rect.intersects_rect(zb):
            zone_hits.append({
                "net": getattr(zone, "net", None),
                "net_name": getattr(zone, "netName", None),
                "layer": getattr(zone, "layer", None),
                "layers": list(getattr(zone, "layers", []) or []),
                "bbox": {"x1": zb.x1, "y1": zb.y1, "x2": zb.x2, "y2": zb.y2},
                "tstamp": getattr(zone, "tstamp", None),
            })

    return {
        "bbox": {"x1": rect.x1, "y1": rect.y1, "x2": rect.x2, "y2": rect.y2},
        "footprints": fp_hits,
        "drawings": drawing_hits,
        "tracks": track_hits,
        "vias": via_hits,
        "zones": zone_hits,
    }


# ---------------------------------------------------------------------------
# query_list
# ---------------------------------------------------------------------------


def query_list(board: Board | str | Path, element: str) -> dict[str, Any]:
    b = _as_board(board)
    element = element.lower()
    if element in ("footprint", "footprints"):
        items = []
        for fp in b.footprints:
            items.append({
                "ref": _ref_of(fp),
                "value": _value_of(fp),
                "lib_id": fp.libId,
                "layer": fp.layer,
                "at": _pos_dict(fp.position),
                "locked": bool(fp.locked),
                "tstamp": fp.tstamp,
            })
        return {"element": "footprints", "items": items}
    if element in ("track", "tracks"):
        items = []
        for item in b.traceItems:
            cls = type(item).__name__
            if cls == "Via":
                continue
            if not (hasattr(item, "start") and hasattr(item, "end")):
                continue
            items.append({
                "kind": cls.lower(),
                "start": {"x": item.start.X, "y": item.start.Y},
                "end": {"x": item.end.X, "y": item.end.Y},
                "width": item.width,
                "layer": item.layer,
                "net": item.net,
                "tstamp": item.tstamp,
            })
        return {"element": "tracks", "items": items}
    if element in ("via", "vias"):
        items = []
        for item in b.traceItems:
            if type(item).__name__ != "Via":
                continue
            items.append({
                "at": {"x": item.position.X, "y": item.position.Y},
                "size": item.size,
                "drill": item.drill,
                "layers": list(getattr(item, "layers", []) or []),
                "net": item.net,
                "tstamp": item.tstamp,
            })
        return {"element": "vias", "items": items}
    if element in ("zone", "zones"):
        items = []
        for zone in b.zones:
            items.append({
                "net": getattr(zone, "net", None),
                "net_name": getattr(zone, "netName", None),
                "layer": getattr(zone, "layer", None),
                "layers": list(getattr(zone, "layers", []) or []),
                "tstamp": getattr(zone, "tstamp", None),
            })
        return {"element": "zones", "items": items}
    if element in ("drawing", "drawings"):
        items = []
        for item in b.graphicItems:
            cls = type(item).__name__
            entry: dict[str, Any] = {
                "kind": cls,
                "layer": getattr(item, "layer", None),
                "tstamp": getattr(item, "tstamp", None),
            }
            bb = _drawing_bbox(item)
            if bb is not None:
                entry["bbox"] = {"x1": bb[0], "y1": bb[1], "x2": bb[2], "y2": bb[3]}
            items.append(entry)
        return {"element": "drawings", "items": items}
    if element in ("net", "nets"):
        items = [{"number": n.number, "name": n.name} for n in b.nets]
        return {"element": "nets", "items": items}
    if element in ("layer", "layers"):
        items = []
        for layer in b.layers:
            items.append({
                "ordinal": getattr(layer, "ordinal", None),
                "name": getattr(layer, "name", None),
                "type": getattr(layer, "type", None),
                "user_name": getattr(layer, "userName", None),
            })
        return {"element": "layers", "items": items}
    return {"element": element, "items": [], "reason": "unknown element"}
