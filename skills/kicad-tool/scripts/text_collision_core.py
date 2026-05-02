from __future__ import annotations

from dataclasses import dataclass


TEXT_TOUCH_TOLERANCE_MM = 1e-3


def rects_overlap_with_area(rect_a: "Rect", rect_b: "Rect", margin: float = 0.0) -> bool:
    a = rect_a.normalized()
    b = rect_b.normalized()
    x_overlap = min(a.x2, b.x2 + margin) - max(a.x1, b.x1 - margin)
    y_overlap = min(a.y2, b.y2 + margin) - max(a.y1, b.y1 - margin)
    return x_overlap > TEXT_TOUCH_TOLERANCE_MM and y_overlap > TEXT_TOUCH_TOLERANCE_MM


@dataclass
class Rect:
    x1: float
    y1: float
    x2: float
    y2: float

    def normalized(self) -> "Rect":
        return Rect(min(self.x1, self.x2), min(self.y1, self.y2), max(self.x1, self.x2), max(self.y1, self.y2))

    @property
    def center(self) -> tuple[float, float]:
        r = self.normalized()
        return ((r.x1 + r.x2) / 2.0, (r.y1 + r.y2) / 2.0)

    def expanded(self, pad_x: float = 0.0, pad_y: float = 0.0) -> "Rect":
        r = self.normalized()
        return Rect(r.x1 - pad_x, r.y1 - pad_y, r.x2 + pad_x, r.y2 + pad_y)

    def union(self, other: "Rect") -> "Rect":
        a = self.normalized()
        b = other.normalized()
        return Rect(min(a.x1, b.x1), min(a.y1, b.y1), max(a.x2, b.x2), max(a.y2, b.y2))

    def intersects_rect(self, other: "Rect", margin: float = 0.0) -> bool:
        a = self.normalized()
        b = other.normalized()
        return not (
            a.x2 < b.x1 - margin
            or a.x1 > b.x2 + margin
            or a.y2 < b.y1 - margin
            or a.y1 > b.y2 + margin
        )

    def intersects_segment(self, p1: tuple[float, float], p2: tuple[float, float], margin: float = 0.0) -> bool:
        r = self.normalized()
        x1, y1 = p1
        x2, y2 = p2
        if abs(x1 - x2) < 1e-9:
            x = x1
            ymin, ymax = sorted((y1, y2))
            return (r.x1 - margin) <= x <= (r.x2 + margin) and ymax >= (r.y1 - margin) and ymin <= (r.y2 + margin)
        if abs(y1 - y2) < 1e-9:
            y = y1
            xmin, xmax = sorted((x1, x2))
            return (r.y1 - margin) <= y <= (r.y2 + margin) and xmax >= (r.x1 - margin) and xmin <= (r.x2 + margin)
        return self.intersects_rect(Rect(x1, y1, x2, y2), margin=margin)


def collision_pad(kind: str | None, owner_key: str | None = None) -> tuple[float, float]:
    if kind == "global_label":
        return (0.30, 0.18)
    if kind == "property":
        if owner_key == "Value":
            return (0.10, 0.10)
        if owner_key == "Reference":
            return (0.06, 0.06)
    return (0.0, 0.0)


def collision_rect(rect: Rect, kind: str | None, owner_key: str | None = None, frame_rect: Rect | None = None) -> Rect:
    if kind == "global_label" and frame_rect is not None:
        return rect.union(frame_rect).normalized()
    pad_x, pad_y = collision_pad(kind, owner_key)
    return rect.expanded(pad_x=pad_x, pad_y=pad_y)


def text_boxes_collide(
    rect_a: Rect,
    kind_a: str | None,
    owner_key_a: str | None,
    rect_b: Rect,
    kind_b: str | None,
    owner_key_b: str | None,
    frame_rect_a: Rect | None = None,
    frame_rect_b: Rect | None = None,
    margin: float = 0.0,
) -> bool:
    return rects_overlap_with_area(
        collision_rect(rect_a, kind_a, owner_key_a, frame_rect=frame_rect_a),
        collision_rect(rect_b, kind_b, owner_key_b, frame_rect=frame_rect_b),
        margin=margin,
    )
