"""Read-only query layer behind ``sch query <element>``.

This module never mutates files. Each ``query_*`` function loads (or accepts) a
``kiutils.schematic.Schematic`` and returns plain ``dict``/``list`` structures
suitable for ``json.dumps``.

Functions correspond 1:1 to the unified subcommand layout in
``docs/plans/20260426-unify-kicad-tool-subcommands.md``:

    sch query symbol --ref REF              -> query_symbol
    sch query pin REF.PIN                   -> query_pin
    sch query net --name N | --pin REF.PIN  -> query_net
    sch query region --bbox X1,Y1,X2,Y2     -> query_region
    sch query wire --uuid|--at|--through    -> query_wire
    sch query label --name|--uuid           -> query_label
    sch query lib-symbol --lib-id LIB_ID    -> query_lib_symbol
    sch query list <element>                -> query_list

Net-membership resolution requires a netlist file (``netlist_path``); the
parser here is a small stand-in until ``sch_netlist.py`` is available.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from kiutils.schematic import Schematic

from kicad_sch_bbox_collisions import (
    SymbolBox,
    WireSeg,
    collect_symbol_boxes,
    collect_wires,
    local_to_schematic,
    rotate_point,
    symbol_pin_local_segments,
)
from text_collision_core import Rect


POINT_EPSILON = 1e-3


# ---------------------------------------------------------------------------
# Loading & shared helpers
# ---------------------------------------------------------------------------


def load_schematic(path: str | Path) -> Schematic:
    return Schematic().from_file(str(path))


def _as_schematic(sch: Schematic | str | Path) -> Schematic:
    if isinstance(sch, Schematic):
        return sch
    return load_schematic(sch)


def _ref_of(sym) -> str | None:
    return next((p.value for p in sym.properties if p.key == "Reference"), None)


def _lib_index(sch: Schematic) -> dict[str, Any]:
    return {s.entryName: s for s in sch.libSymbols}


def _approx(a: float, b: float, eps: float = POINT_EPSILON) -> bool:
    return abs(a - b) <= eps


def _approx_point(p: tuple[float, float], q: tuple[float, float], eps: float = POINT_EPSILON) -> bool:
    return _approx(p[0], q[0], eps) and _approx(p[1], q[1], eps)


def _point_on_segment(pt: tuple[float, float], a: tuple[float, float], b: tuple[float, float], eps: float = POINT_EPSILON) -> bool:
    # Cross product near zero AND projection within bounds.
    ax, ay = a
    bx, by = b
    px, py = pt
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if abs(cross) > eps * max(1.0, ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5):
        return False
    if min(ax, bx) - eps <= px <= max(ax, bx) + eps and min(ay, by) - eps <= py <= max(ay, by) + eps:
        return True
    return False


def _split_ref_pin(ref_pin: str) -> tuple[str, str]:
    if "." not in ref_pin:
        raise ValueError(f"expected REF.PIN, got {ref_pin!r}")
    ref, pin = ref_pin.split(".", 1)
    return ref, pin


# ---------------------------------------------------------------------------
# query_lib_symbol
# ---------------------------------------------------------------------------


def _lib_pin_endpoint(pin) -> tuple[float, float]:
    sx, sy = pin.position.X, pin.position.Y
    angle = int(pin.position.angle or 0) % 360
    length = float(pin.length or 0)
    if angle == 0:
        return (sx + length, sy)
    if angle == 90:
        return (sx, sy + length)
    if angle == 180:
        return (sx - length, sy)
    if angle == 270:
        return (sx, sy - length)
    return (sx, sy)


def _lib_pin_dict(pin) -> dict[str, Any]:
    return {
        "number": getattr(pin, "number", None),
        "name": getattr(pin, "name", None),
        "electrical_type": getattr(pin, "electricalType", None),
        "graphical_style": getattr(pin, "graphicalStyle", None),
        "length": float(pin.length or 0),
        "position": {
            "x": pin.position.X,
            "y": pin.position.Y,
            "angle": int(pin.position.angle or 0),
        },
        "endpoint": {"x": _lib_pin_endpoint(pin)[0], "y": _lib_pin_endpoint(pin)[1]},
    }


def query_lib_symbol(sch: Schematic | str | Path, lib_id: str) -> dict[str, Any]:
    s = _as_schematic(sch)
    # lib_id may be ``"lib:entry"`` or ``"entry"``.
    entry = lib_id.split(":", 1)[1] if ":" in lib_id else lib_id
    libsym = _lib_index(s).get(lib_id) or _lib_index(s).get(entry)
    if libsym is None:
        return {"found": False, "lib_id": lib_id, "pins": []}
    pins: list[dict[str, Any]] = []
    for unit in getattr(libsym, "units", []):
        for pin in getattr(unit, "pins", []):
            pins.append(_lib_pin_dict(pin))
    return {
        "found": True,
        "lib_id": libsym.entryName,
        "pins": pins,
    }


# ---------------------------------------------------------------------------
# query_symbol & query_pin
# ---------------------------------------------------------------------------


def _symbol_absolute_pins(sym, libsym) -> list[dict[str, Any]]:
    if libsym is None:
        return []
    sx, sy = sym.position.X, sym.position.Y
    angle = int(sym.position.angle or 0)
    out: list[dict[str, Any]] = []
    for unit in getattr(libsym, "units", []):
        for pin in getattr(unit, "pins", []):
            local = (pin.position.X, pin.position.Y)
            absxy = local_to_schematic(sx, sy, angle, local[0], local[1])
            end_local = _lib_pin_endpoint(pin)
            end_abs = local_to_schematic(sx, sy, angle, end_local[0], end_local[1])
            out.append({
                "number": getattr(pin, "number", None),
                "name": getattr(pin, "name", None),
                "electrical_type": getattr(pin, "electricalType", None),
                "graphical_style": getattr(pin, "graphicalStyle", None),
                "length": float(pin.length or 0),
                "absolute": {"x": absxy[0], "y": absxy[1]},
                "endpoint_absolute": {"x": end_abs[0], "y": end_abs[1]},
                "lib_position": {
                    "x": pin.position.X,
                    "y": pin.position.Y,
                    "angle": int(pin.position.angle or 0),
                },
            })
    return out


def _find_symbol(sch: Schematic, ref: str):
    for sym in sch.schematicSymbols:
        if _ref_of(sym) == ref:
            return sym
    return None


def query_symbol(sch: Schematic | str | Path, ref: str) -> dict[str, Any]:
    s = _as_schematic(sch)
    sym = _find_symbol(s, ref)
    if sym is None:
        return {"found": False, "ref": ref}
    libsym = _lib_index(s).get(sym.entryName)
    properties = []
    for prop in sym.properties:
        properties.append({
            "key": prop.key,
            "value": prop.value,
            "at": {
                "x": prop.position.X,
                "y": prop.position.Y,
                "angle": int(prop.position.angle or 0),
            },
            "hide": bool(getattr(prop.effects, "hide", False)) if prop.effects else False,
        })
    return {
        "found": True,
        "ref": ref,
        "lib_id": sym.libId,
        "at": {
            "x": sym.position.X,
            "y": sym.position.Y,
            "angle": int(sym.position.angle or 0),
        },
        "uuid": getattr(sym, "uuid", None),
        "properties": properties,
        "pins": _symbol_absolute_pins(sym, libsym),
    }


def query_pin(
    sch: Schematic | str | Path,
    ref_pin: str,
    netlist_path: str | Path | None = None,
) -> dict[str, Any]:
    s = _as_schematic(sch)
    ref, pin_id = _split_ref_pin(ref_pin)
    sym = _find_symbol(s, ref)
    if sym is None:
        return {"found": False, "ref": ref, "pin": pin_id}
    libsym = _lib_index(s).get(sym.entryName)
    pins = _symbol_absolute_pins(sym, libsym)
    pin = next((p for p in pins if p["number"] == pin_id), None)
    if pin is None:
        return {"found": False, "ref": ref, "pin": pin_id, "reason": "pin not in lib symbol"}
    result: dict[str, Any] = {
        "found": True,
        "ref": ref,
        "pin": pin_id,
        "lib_id": sym.libId,
        "absolute": pin["absolute"],
        "endpoint_absolute": pin["endpoint_absolute"],
        "electrical_type": pin["electrical_type"],
        "name": pin["name"],
        "length": pin["length"],
        "net": None,
    }
    if netlist_path is not None:
        net = _net_of_pin_from_netlist(netlist_path, ref, pin_id)
        result["net"] = net
    return result


# ---------------------------------------------------------------------------
# Netlist parsing (minimal stand-in for sch_netlist.py)
# ---------------------------------------------------------------------------


_NET_BLOCK_RE = re.compile(
    r"\(net\s+\(code\s+\"?([^\")]+)\"?\)\s*\(name\s+\"([^\"]*)\"\)(.*?)\)\s*(?=\(net|\)\s*\)\s*$|\(net|\Z)",
    re.DOTALL,
)
_NODE_RE = re.compile(
    r"\(node\s+\(ref\s+\"([^\"]+)\"\)\s+\(pin\s+\"([^\"]+)\"\)(?:\s+\(pinfunction\s+\"([^\"]*)\"\))?(?:\s+\(pintype\s+\"([^\"]*)\"\))?",
)


def _parse_netlist(path: str | Path) -> list[dict[str, Any]]:
    """TODO: replace with sch_netlist.py once that module exists."""
    text = Path(path).read_text()
    nets: list[dict[str, Any]] = []
    # More robust: walk net blocks one by one.
    i = 0
    while True:
        idx = text.find("(net ", i)
        if idx < 0:
            break
        # find matching close paren
        depth = 0
        j = idx
        while j < len(text):
            c = text[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        block = text[idx:j]
        i = j
        m_code = re.search(r"\(code\s+\"?([^\")\s]+)\"?\)", block)
        m_name = re.search(r"\(name\s+\"([^\"]*)\"\)", block)
        if not m_name:
            continue
        nodes: list[dict[str, Any]] = []
        for nm in _NODE_RE.finditer(block):
            nodes.append({
                "ref": nm.group(1),
                "pin": nm.group(2),
                "pin_function": nm.group(3),
                "pin_type": nm.group(4),
            })
        nets.append({
            "code": m_code.group(1) if m_code else None,
            "name": m_name.group(1),
            "nodes": nodes,
        })
    return nets


def _net_of_pin_from_netlist(path: str | Path, ref: str, pin: str) -> dict[str, Any] | None:
    for net in _parse_netlist(path):
        for node in net["nodes"]:
            if node["ref"] == ref and node["pin"] == pin:
                return {"name": net["name"], "code": net["code"], "nodes": net["nodes"]}
    return None


# ---------------------------------------------------------------------------
# query_net
# ---------------------------------------------------------------------------


def query_net(
    sch: Schematic | str | Path,  # unused; kept for symmetry
    netlist_path: str | Path,
    name: str | None = None,
    pin: str | None = None,
) -> dict[str, Any]:
    if name is None and pin is None:
        return {"found": False, "reason": "either name or pin required"}
    nets = _parse_netlist(netlist_path)
    if name is not None:
        match = next((n for n in nets if n["name"] == name), None)
        if match is None:
            return {"found": False, "name": name}
        return {"found": True, **match}
    ref, pin_id = _split_ref_pin(pin)  # type: ignore[arg-type]
    for net in nets:
        for node in net["nodes"]:
            if node["ref"] == ref and node["pin"] == pin_id:
                return {"found": True, **net}
    return {"found": False, "ref": ref, "pin": pin_id}


# ---------------------------------------------------------------------------
# query_region
# ---------------------------------------------------------------------------


def _label_iter(sch: Schematic) -> Iterable[tuple[str, Any]]:
    for lab in sch.labels:
        yield ("local", lab)
    for lab in sch.globalLabels:
        yield ("global", lab)
    for lab in sch.hierarchicalLabels:
        yield ("hier", lab)


def query_region(sch: Schematic | str | Path, bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    s = _as_schematic(sch)
    rect = Rect(bbox[0], bbox[1], bbox[2], bbox[3]).normalized()

    sym_hits: list[dict[str, Any]] = []
    for box in collect_symbol_boxes(s):
        if rect.intersects_rect(box.bbox):
            sym_hits.append({
                "ref": box.ref,
                "lib_id": box.lib_id,
                "bbox": {"x1": box.bbox.x1, "y1": box.bbox.y1, "x2": box.bbox.x2, "y2": box.bbox.y2},
            })

    wire_hits: list[dict[str, Any]] = []
    for w in collect_wires(s):
        if rect.intersects_segment(w.p1, w.p2):
            wire_hits.append({"uuid": w.uuid, "p1": list(w.p1), "p2": list(w.p2)})

    label_hits: list[dict[str, Any]] = []
    for kind, lab in _label_iter(s):
        x, y = lab.position.X, lab.position.Y
        if rect.x1 <= x <= rect.x2 and rect.y1 <= y <= rect.y2:
            label_hits.append({
                "kind": kind,
                "text": lab.text,
                "uuid": lab.uuid,
                "at": {"x": x, "y": y, "angle": int(lab.position.angle or 0)},
            })

    junction_hits: list[dict[str, Any]] = []
    for j in s.junctions:
        x, y = j.position.X, j.position.Y
        if rect.x1 <= x <= rect.x2 and rect.y1 <= y <= rect.y2:
            junction_hits.append({"uuid": j.uuid, "at": {"x": x, "y": y}})

    return {
        "bbox": {"x1": rect.x1, "y1": rect.y1, "x2": rect.x2, "y2": rect.y2},
        "symbols": sym_hits,
        "wires": wire_hits,
        "labels": label_hits,
        "junctions": junction_hits,
    }


# ---------------------------------------------------------------------------
# query_wire
# ---------------------------------------------------------------------------


def _wire_dict(w: WireSeg) -> dict[str, Any]:
    return {"uuid": w.uuid, "p1": list(w.p1), "p2": list(w.p2)}


def query_wire(
    sch: Schematic | str | Path,
    uuid: str | None = None,
    at: tuple[float, float] | None = None,
    through: tuple[float, float] | None = None,
) -> dict[str, Any]:
    s = _as_schematic(sch)
    wires = collect_wires(s)
    if uuid is not None:
        match = [w for w in wires if w.uuid == uuid]
    elif at is not None:
        match = [w for w in wires if _approx_point(w.p1, at) or _approx_point(w.p2, at)]
    elif through is not None:
        match = [w for w in wires if _point_on_segment(through, w.p1, w.p2)]
    else:
        return {"found": False, "reason": "one of uuid/at/through required"}
    return {"found": bool(match), "wires": [_wire_dict(w) for w in match]}


# ---------------------------------------------------------------------------
# query_label
# ---------------------------------------------------------------------------


def query_label(
    sch: Schematic | str | Path,
    name: str | None = None,
    uuid: str | None = None,
) -> dict[str, Any]:
    s = _as_schematic(sch)
    if name is None and uuid is None:
        return {"found": False, "reason": "name or uuid required"}
    matches: list[dict[str, Any]] = []
    for kind, lab in _label_iter(s):
        if name is not None and lab.text != name:
            continue
        if uuid is not None and lab.uuid != uuid:
            continue
        matches.append({
            "kind": kind,
            "text": lab.text,
            "uuid": lab.uuid,
            "at": {
                "x": lab.position.X,
                "y": lab.position.Y,
                "angle": int(lab.position.angle or 0),
            },
            "shape": getattr(lab, "shape", None),
        })
    return {"found": bool(matches), "labels": matches}


# ---------------------------------------------------------------------------
# query_list
# ---------------------------------------------------------------------------


def query_list(
    sch: Schematic | str | Path,
    element: str,
    netlist_path: str | Path | None = None,
) -> dict[str, Any]:
    s = _as_schematic(sch)
    element = element.lower()
    if element in ("symbol", "symbols"):
        items = []
        for sym in s.schematicSymbols:
            items.append({
                "ref": _ref_of(sym),
                "lib_id": sym.libId,
                "at": {
                    "x": sym.position.X,
                    "y": sym.position.Y,
                    "angle": int(sym.position.angle or 0),
                },
                "uuid": getattr(sym, "uuid", None),
            })
        return {"element": "symbols", "items": items}
    if element in ("label", "labels"):
        items = []
        for kind, lab in _label_iter(s):
            items.append({
                "kind": kind,
                "text": lab.text,
                "uuid": lab.uuid,
                "at": {
                    "x": lab.position.X,
                    "y": lab.position.Y,
                    "angle": int(lab.position.angle or 0),
                },
            })
        return {"element": "labels", "items": items}
    if element in ("wire", "wires"):
        return {"element": "wires", "items": [_wire_dict(w) for w in collect_wires(s)]}
    if element in ("junction", "junctions"):
        items = [
            {"uuid": j.uuid, "at": {"x": j.position.X, "y": j.position.Y}}
            for j in s.junctions
        ]
        return {"element": "junctions", "items": items}
    if element in ("net", "nets"):
        if netlist_path is None:
            return {"element": "nets", "items": [], "reason": "netlist_path required"}
        nets = _parse_netlist(netlist_path)
        return {"element": "nets", "items": nets}
    return {"element": element, "items": [], "reason": "unknown element"}
