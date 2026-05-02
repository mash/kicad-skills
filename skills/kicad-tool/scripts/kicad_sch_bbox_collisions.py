from __future__ import annotations

import functools
import math
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from xml.etree import ElementTree as ET

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, "/Users/mash/src/github.com/mvnmgrx/kiutils/src")

from kiutils.schematic import Schematic
from text_collision_core import Rect, collision_rect, text_boxes_collide


@dataclass
class TextTarget:
    text: str
    kind: str
    expected_x: float
    expected_y: float
    angle: int
    owner_ref: str | None = None
    owner_key: str | None = None


@dataclass
class RenderedText:
    text: str
    anchor_x: float
    anchor_y: float
    text_anchor: str
    bbox: Rect
    frame_bbox: Rect | None = None
    matched_kind: str | None = None
    matched_ref: str | None = None
    matched_key: str | None = None
    matched_x: float | None = None
    matched_y: float | None = None


@dataclass
class WireSeg:
    p1: tuple[float, float]
    p2: tuple[float, float]
    uuid: str


@dataclass
class SymbolBox:
    ref: str
    lib_id: str
    bbox: Rect
    pin_points: list[tuple[float, float]]
    pin_segments: list[tuple[tuple[float, float], tuple[float, float]]]


GLOBAL_LABEL_PIN_CLEARANCE_MM = 0.25
GLOBAL_LABEL_WIRE_ENTRY_TOLERANCE_MM = 0.05


def svg_tag(elem) -> str:
    return elem.tag.split("}")[-1]


def rotate_point(px: float, py: float, angle: int) -> tuple[float, float]:
    angle %= 360
    if angle == 0:
        return px, py
    if angle == 90:
        return -py, px
    if angle == 180:
        return -px, -py
    if angle == 270:
        return py, -px
    rad = math.radians(angle)
    return (px * math.cos(rad) - py * math.sin(rad), px * math.sin(rad) + py * math.cos(rad))


def local_to_schematic(sym_x: float, sym_y: float, sym_angle: int, px: float, py: float) -> tuple[float, float]:
    rx, ry = rotate_point(px, py, sym_angle)
    return (sym_x + rx, sym_y - ry)


def parse_path_bbox(path_elem) -> Rect | None:
    coords = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", path_elem.attrib.get("d", ""))]
    if len(coords) < 4:
        return None
    xs = coords[0::2]
    ys = coords[1::2]
    return Rect(min(xs), min(ys), max(xs), max(ys))


def stroked_text_bbox(stroke_group) -> Rect | None:
    coords: list[float] = []
    for p in stroke_group.iter():
        if svg_tag(p) != "path":
            continue
        coords.extend(float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", p.attrib.get("d", "")))
    if len(coords) < 4:
        return None
    xs = coords[0::2]
    ys = coords[1::2]
    return Rect(min(xs), min(ys), max(xs), max(ys))


def text_child_from_group(group):
    children = list(group)
    if len(children) != 1:
        return None
    child = children[0]
    if svg_tag(child) != "text":
        return None
    return child


def frame_bbox_for_sibling_path(path_elem) -> Rect | None:
    style = path_elem.attrib.get("style", "")
    d = path_elem.attrib.get("d", "")
    if "stroke:#840000" not in style:
        return None
    if "fill:none" not in style:
        return None
    if "Z" not in d and "z" not in d:
        return None
    return parse_path_bbox(path_elem)


def parse_svg_rendered_texts(svg_path: Path) -> list[RenderedText]:
    root = ET.parse(svg_path).getroot()
    rendered: list[RenderedText] = []

    def walk(node) -> None:
        children = list(node)
        for idx, child in enumerate(children):
            text_elem = None
            if svg_tag(child) == "text":
                text_elem = child
            elif svg_tag(child) == "g":
                text_elem = text_child_from_group(child)

            if text_elem is not None and idx + 1 < len(children):
                stroke_group = children[idx + 1]
                if svg_tag(stroke_group) == "g" and stroke_group.attrib.get("class") == "stroked-text":
                    text = (text_elem.text or "").strip()
                    desc = next((c for c in list(stroke_group) if svg_tag(c) == "desc"), None)
                    bbox = stroked_text_bbox(stroke_group)
                    if text and desc is not None and (desc.text or "").strip() == text and bbox is not None:
                        frame_bbox = None
                        if idx + 2 < len(children) and svg_tag(children[idx + 2]) == "path":
                            frame_bbox = frame_bbox_for_sibling_path(children[idx + 2])
                        rendered.append(
                            RenderedText(
                                text=text,
                                anchor_x=float(text_elem.attrib["x"]),
                                anchor_y=float(text_elem.attrib["y"]),
                                text_anchor=text_elem.attrib.get("text-anchor", "start"),
                                bbox=bbox,
                                frame_bbox=frame_bbox,
                            )
                        )

            if svg_tag(child) == "g" and child.attrib.get("class") != "stroked-text" and text_child_from_group(child) is None:
                walk(child)

    walk(root)
    deduped: list[RenderedText] = []
    seen: set[tuple] = set()
    for rt in rendered:
        frame = rt.frame_bbox.normalized() if rt.frame_bbox else None
        key = (
            rt.text,
            round(rt.anchor_x, 4),
            round(rt.anchor_y, 4),
            rt.text_anchor,
            round(rt.bbox.x1, 4),
            round(rt.bbox.y1, 4),
            round(rt.bbox.x2, 4),
            round(rt.bbox.y2, 4),
            None if frame is None else round(frame.x1, 4),
            None if frame is None else round(frame.y1, 4),
            None if frame is None else round(frame.x2, 4),
            None if frame is None else round(frame.y2, 4),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(rt)
    return deduped


def collect_text_targets(sch: Schematic) -> list[TextTarget]:
    targets: list[TextTarget] = []
    for gl in sch.globalLabels:
        targets.append(TextTarget(text=gl.text, kind="global_label", expected_x=gl.position.X, expected_y=gl.position.Y, angle=int(gl.position.angle or 0)))
    for sym in sch.schematicSymbols:
        ref = next((p.value for p in sym.properties if p.key == "Reference"), None)
        for prop in sym.properties:
            if prop.key not in {"Reference", "Value"}:
                continue
            if getattr(prop.effects, "hide", False):
                continue
            targets.append(
                TextTarget(
                    text=prop.value,
                    kind="property",
                    expected_x=prop.position.X,
                    expected_y=prop.position.Y,
                    angle=int(prop.position.angle or 0),
                    owner_ref=ref,
                    owner_key=prop.key,
                )
            )
    return targets


def rendered_target_distance(rt: RenderedText, target: TextTarget) -> float:
    if target.kind == "global_label" and rt.frame_bbox is not None:
        frame = rt.frame_bbox.normalized()
        cy = (frame.y1 + frame.y2) / 2.0
        candidate_points = [(frame.x1, cy), (frame.x2, cy)]
        px, py = min(candidate_points, key=lambda p: math.hypot(p[0] - target.expected_x, p[1] - target.expected_y))
    else:
        px, py = rt.anchor_x, rt.anchor_y
    return math.hypot(px - target.expected_x, py - target.expected_y)


def match_rendered_to_targets(rendered: list[RenderedText], targets: list[TextTarget]) -> list[RenderedText]:
    rendered_by_text: dict[str, list[RenderedText]] = {}
    targets_by_text: dict[str, list[TextTarget]] = {}
    for rt in rendered:
        rendered_by_text.setdefault(rt.text, []).append(rt)
    for target in targets:
        targets_by_text.setdefault(target.text, []).append(target)

    for text, text_targets in targets_by_text.items():
        text_rendered = rendered_by_text.get(text, [])
        kinds = {target.kind for target in text_targets}
        if kinds == {"global_label"}:
            text_rendered = [rt for rt in text_rendered if rt.frame_bbox is not None]
        elif kinds == {"property"}:
            text_rendered = [rt for rt in text_rendered if rt.frame_bbox is None]
        if not text_rendered:
            continue
        dist_matrix = [
            [rendered_target_distance(rt, target) for rt in text_rendered]
            for target in text_targets
        ]
        target_count = len(text_targets)
        rendered_count = len(text_rendered)
        penalty = 1e6

        @functools.lru_cache(maxsize=None)
        def solve(target_idx: int, used_mask: int) -> tuple[float, tuple[tuple[int, int], ...]]:
            if target_idx >= target_count:
                return 0.0, ()

            best_cost, best_pairs = solve(target_idx + 1, used_mask)
            best_cost += penalty

            for rendered_idx in range(rendered_count):
                if used_mask & (1 << rendered_idx):
                    continue
                tail_cost, tail_pairs = solve(target_idx + 1, used_mask | (1 << rendered_idx))
                cost = dist_matrix[target_idx][rendered_idx] + tail_cost
                if cost < best_cost:
                    best_cost = cost
                    best_pairs = ((target_idx, rendered_idx),) + tail_pairs
            return best_cost, best_pairs

        _, pairs = solve(0, 0)
        for target_idx, rendered_idx in pairs:
            target = text_targets[target_idx]
            rt = text_rendered[rendered_idx]
            rt.matched_kind = target.kind
            rt.matched_ref = target.owner_ref
            rt.matched_key = target.owner_key
            rt.matched_x = target.expected_x
            rt.matched_y = target.expected_y

    return rendered


def collect_wires(sch: Schematic) -> list[WireSeg]:
    wires: list[WireSeg] = []
    for gi in sch.graphicalItems:
        if gi.__class__.__name__ != "Connection":
            continue
        if getattr(gi, "type", None) != "wire":
            continue
        if len(gi.points) != 2:
            continue
        wires.append(WireSeg(p1=(gi.points[0].X, gi.points[0].Y), p2=(gi.points[1].X, gi.points[1].Y), uuid=gi.uuid))
    return wires


def symbol_body_local_points(lib_symbol) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for unit in getattr(lib_symbol, "units", []):
        for g in getattr(unit, "graphicItems", []):
            if g.__class__.__name__ == "SyRect":
                pts.extend([(g.start.X, g.start.Y), (g.start.X, g.end.Y), (g.end.X, g.start.Y), (g.end.X, g.end.Y)])
    return pts


def symbol_pin_local_points(lib_symbol) -> list[tuple[float, float]]:
    return [point for segment in symbol_pin_local_segments(lib_symbol) for point in segment]


def symbol_pin_local_segments(lib_symbol) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for unit in getattr(lib_symbol, "units", []):
        for pin in getattr(unit, "pins", []):
            sx, sy = pin.position.X, pin.position.Y
            angle = int(pin.position.angle or 0) % 360
            length = float(pin.length or 0)
            if angle == 0:
                ex, ey = sx + length, sy
            elif angle == 90:
                ex, ey = sx, sy + length
            elif angle == 180:
                ex, ey = sx - length, sy
            elif angle == 270:
                ex, ey = sx, sy - length
            else:
                ex, ey = sx, sy
            segments.append(((sx, sy), (ex, ey)))
    return segments


def collect_symbol_boxes(sch: Schematic) -> list[SymbolBox]:
    lib = {s.entryName: s for s in sch.libSymbols}
    boxes: list[SymbolBox] = []
    for sym in sch.schematicSymbols:
        ref = next((p.value for p in sym.properties if p.key == "Reference"), None)
        if not ref:
            continue
        libsym = lib.get(sym.entryName)
        if not libsym:
            continue
        local_pts = symbol_body_local_points(libsym)
        if not local_pts:
            local_pts = symbol_pin_local_points(libsym)
        if not local_pts:
            continue
        sch_pts = [local_to_schematic(sym.position.X, sym.position.Y, int(sym.position.angle or 0), px, py) for px, py in local_pts]
        pin_pts = [
            local_to_schematic(sym.position.X, sym.position.Y, int(sym.position.angle or 0), px, py)
            for px, py in symbol_pin_local_points(libsym)
        ]
        pin_segments = [
            (
                local_to_schematic(sym.position.X, sym.position.Y, int(sym.position.angle or 0), p1[0], p1[1]),
                local_to_schematic(sym.position.X, sym.position.Y, int(sym.position.angle or 0), p2[0], p2[1]),
            )
            for p1, p2 in symbol_pin_local_segments(libsym)
        ]
        xs = [p[0] for p in sch_pts]
        ys = [p[1] for p in sch_pts]
        boxes.append(
            SymbolBox(
                ref=ref,
                lib_id=sym.libId,
                bbox=Rect(min(xs), min(ys), max(xs), max(ys)),
                pin_points=pin_pts,
                pin_segments=pin_segments,
            )
        )
    return boxes


def segment_overlap_inside_rect(rect: Rect, p1: tuple[float, float], p2: tuple[float, float], margin: float = 0.0) -> float:
    r = rect.normalized()
    x1, y1 = p1
    x2, y2 = p2
    if abs(x1 - x2) < 1e-9:
        x = x1
        if not (r.x1 - margin <= x <= r.x2 + margin):
            return 0.0
        ymin, ymax = sorted((y1, y2))
        overlap = min(ymax, r.y2 + margin) - max(ymin, r.y1 - margin)
        return max(0.0, overlap)
    if abs(y1 - y2) < 1e-9:
        y = y1
        if not (r.y1 - margin <= y <= r.y2 + margin):
            return 0.0
        xmin, xmax = sorted((x1, x2))
        overlap = min(xmax, r.x2 + margin) - max(xmin, r.x1 - margin)
        return max(0.0, overlap)
    if not rect.intersects_segment(p1, p2, margin=margin):
        return 0.0
    return math.hypot(x2 - x1, y2 - y1)


def is_global_label_endpoint_touch(rt: RenderedText, hitbox: Rect, wire: WireSeg, margin: float) -> bool:
    if rt.matched_kind != "global_label" or rt.matched_x is None or rt.matched_y is None:
        return False
    endpoint_matches = (
        (abs(wire.p1[0] - rt.matched_x) < 0.01 and abs(wire.p1[1] - rt.matched_y) < 0.01)
        or (abs(wire.p2[0] - rt.matched_x) < 0.01 and abs(wire.p2[1] - rt.matched_y) < 0.01)
    )
    if not endpoint_matches:
        return False
    overlap = segment_overlap_inside_rect(hitbox, wire.p1, wire.p2, margin=margin)
    rect = hitbox.normalized()
    if abs(wire.p1[0] - wire.p2[0]) < 1e-9:
        allowed = (rect.y2 - rect.y1) / 2.0 + GLOBAL_LABEL_WIRE_ENTRY_TOLERANCE_MM
    elif abs(wire.p1[1] - wire.p2[1]) < 1e-9:
        allowed = (rect.x2 - rect.x1) / 2.0 + GLOBAL_LABEL_WIRE_ENTRY_TOLERANCE_MM
    else:
        allowed = GLOBAL_LABEL_WIRE_ENTRY_TOLERANCE_MM
    return overlap <= allowed


def detect_collisions(rendered_texts: list[RenderedText], wires: list[WireSeg], boxes: list[SymbolBox], margin: float) -> list[dict]:
    collisions: list[dict] = []
    for rt in rendered_texts:
        if rt.matched_kind is None:
            continue
        hitbox = collision_rect(rt.bbox, rt.matched_kind, rt.matched_key, frame_rect=rt.frame_bbox)
        entry = {
            "text": rt.text,
            "kind": rt.matched_kind,
            "owner_ref": rt.matched_ref,
            "owner_key": rt.matched_key,
            "matched_x": rt.matched_x,
            "matched_y": rt.matched_y,
            "anchor_x": rt.anchor_x,
            "anchor_y": rt.anchor_y,
            "bbox": asdict(rt.bbox.normalized()),
            "frame_bbox": asdict(rt.frame_bbox.normalized()) if rt.frame_bbox else None,
            "collision_bbox": asdict(hitbox.normalized()),
            "wire_hits": [],
            "component_hits": [],
            "text_hits": [],
        }
        for w in wires:
            if is_global_label_endpoint_touch(rt, hitbox, w, margin=margin):
                continue
            if hitbox.intersects_segment(w.p1, w.p2, margin=margin):
                entry["wire_hits"].append({"uuid": w.uuid, "p1": w.p1, "p2": w.p2})
        for b in boxes:
            # Border-only: text crossing the symbol body's outline is a hit.
            # Text fully contained inside the body (IC interior) is readable.
            if hitbox.intersects_rect(b.bbox, margin=margin):
                t = hitbox.normalized()
                rb = b.bbox.normalized()
                fully_inside = (
                    t.x1 >= rb.x1 - margin and t.x2 <= rb.x2 + margin
                    and t.y1 >= rb.y1 - margin and t.y2 <= rb.y2 + margin
                )
                if not fully_inside:
                    entry["component_hits"].append({"ref": b.ref, "lib_id": b.lib_id, "bbox": asdict(b.bbox.normalized())})
            if rt.matched_kind == "global_label":
                pin_margin = max(margin, GLOBAL_LABEL_PIN_CLEARANCE_MM)
                anchor_xy = (rt.matched_x, rt.matched_y)
                for p1, p2 in b.pin_segments:
                    # If the global_label anchors directly on a pin endpoint,
                    # the label hitbox unavoidably overlaps that pin segment;
                    # this is correct topology, not a collision.
                    if anchor_xy[0] is not None and anchor_xy[1] is not None and (
                        (abs(p1[0] - anchor_xy[0]) < 0.01 and abs(p1[1] - anchor_xy[1]) < 0.01)
                        or (abs(p2[0] - anchor_xy[0]) < 0.01 and abs(p2[1] - anchor_xy[1]) < 0.01)
                    ):
                        continue
                    if hitbox.intersects_segment(p1, p2, margin=pin_margin):
                        entry["component_hits"].append(
                            {
                                "ref": b.ref,
                                "lib_id": b.lib_id,
                                "kind": "pin_segment",
                                "p1": p1,
                                "p2": p2,
                            }
                        )
                        break
        for other in rendered_texts:
            if other is rt:
                continue
            if text_boxes_collide(
                rt.bbox, rt.matched_kind, rt.matched_key,
                other.bbox, other.matched_kind, other.matched_key,
                frame_rect_a=rt.frame_bbox, frame_rect_b=other.frame_bbox,
                margin=margin,
            ):
                entry["text_hits"].append({"text": other.text, "owner_ref": other.matched_ref, "owner_key": other.matched_key, "anchor_x": other.anchor_x, "anchor_y": other.anchor_y})
        if entry["wire_hits"] or entry["component_hits"] or entry["text_hits"]:
            collisions.append(entry)
    return collisions
