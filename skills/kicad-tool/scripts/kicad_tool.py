#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
# Resolve relative paths against the caller's CWD, not the script's location.
# This keeps the script working when installed as a plugin (where the script
# lives outside the user's project tree) as well as when run in-tree.
REPO_ROOT = Path.cwd()
DEFAULT_KICAD_CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import score_schematic as schematic_score
import sch_query
import sch_edit
import sch_netlist
import pcb_query
import pcb_edit
import pcb_netlist


def kicad_cli() -> str:
    return os.environ.get("KICAD_CLI", DEFAULT_KICAD_CLI)


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def command_result(name: str, args: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        args,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return {
        "command": name,
        "argv": args,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def run_checked(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def render_svg(schematic: Path, outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    run_checked([kicad_cli(), "sch", "export", "svg", "--output", str(outdir), str(schematic)])
    expected = outdir / f"{schematic.stem}.svg"
    if expected.exists():
        return expected
    svgs = sorted(outdir.glob("*.svg"))
    if not svgs:
        raise FileNotFoundError(f"no SVG exported into {outdir}")
    return svgs[0]


_SVG_OPEN_TAG_RE = re.compile(r"<svg\b[^>]*>", re.DOTALL)
_SVG_ATTR_RE = re.compile(r'(width|height|viewBox)\s*=\s*"[^"]*"')


def _rewrite_svg_viewbox(svg_text: str, x_mm: float, y_mm: float, w_mm: float, h_mm: float) -> str:
    match = _SVG_OPEN_TAG_RE.search(svg_text)
    if not match:
        raise ValueError("could not locate <svg ...> tag in SVG output")
    tag = match.group(0)
    new_attrs = (
        f'width="{w_mm:.4f}mm" height="{h_mm:.4f}mm" '
        f'viewBox="{x_mm:.4f} {y_mm:.4f} {w_mm:.4f} {h_mm:.4f}"'
    )
    stripped = _SVG_ATTR_RE.sub("", tag)
    rebuilt = stripped.rstrip(">").rstrip() + " " + new_attrs + ">"
    return svg_text[: match.start()] + rebuilt + svg_text[match.end():]


PX_PER_MM = 48.0


def render_region(
    schematic: Path,
    bbox_mm: tuple[float, float, float, float],
    out: Path,
) -> Path:
    x1, y1, x2, y2 = bbox_mm
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1
    x, y, w, h = x1, y1, x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        raise ValueError(f"empty bbox: {bbox_mm}")

    rsvg = subprocess.run(["which", "rsvg-convert"], capture_output=True, text=True)
    if rsvg.returncode != 0:
        raise RuntimeError("rsvg-convert is required for render-region; install librsvg")

    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kicad-tool-region-") as tmpdir:
        full_svg = render_svg(schematic, Path(tmpdir))
        original = full_svg.read_text(encoding="utf-8")
        cropped = _rewrite_svg_viewbox(original, x, y, w, h)
        cropped_path = Path(tmpdir) / "region.svg"
        cropped_path.write_text(cropped, encoding="utf-8")

        px_w = max(1, int(round(w * PX_PER_MM)))
        run_checked([
            "rsvg-convert",
            "-w", str(px_w),
            "-o", str(out),
            str(cropped_path),
        ])
    return out


def inspect_schematic(schematic: Path, only_text: str | None, margin: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="kicad-tool-inspect-") as tmpdir:
        svg = render_svg(schematic, Path(tmpdir))
        payload = schematic_score.score_schematic_from_svg(schematic, svg, margin=margin)

    collisions = payload["collisions"]
    if only_text:
        pattern = re.compile(only_text)
        collisions = [collision for collision in collisions if pattern.search(collision["text"])]

    return {
        "score": payload["score"],
        "wire_length": payload["wire_length"],
        "collisions": collisions,
        "symbol_wire_conflicts": payload["symbol_wire_conflicts"],
        "filters": {
            "only_text": only_text,
            "margin": margin,
        },
    }


def parse_xy(s: str) -> tuple[float, float]:
    parts = s.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"expected X,Y, got {s!r}")
    return float(parts[0]), float(parts[1])


def parse_xy_rot(s: str) -> tuple[float, float, float]:
    parts = s.split(",")
    if len(parts) not in (2, 3):
        raise argparse.ArgumentTypeError(f"expected X,Y[,ROT], got {s!r}")
    x, y = float(parts[0]), float(parts[1])
    rot = float(parts[2]) if len(parts) == 3 else 0.0
    return x, y, rot


def parse_bbox(s: str) -> tuple[float, float, float, float]:
    parts = s.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(f"expected X1,Y1,X2,Y2, got {s!r}")
    return float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])


def cmd_sch_render_region(args: argparse.Namespace) -> int:
    try:
        png = render_region(args.schematic, args.bbox, args.output)
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr or "")
        return exc.returncode
    except (RuntimeError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    print(png)
    return 0


def cmd_sch_inspect(args: argparse.Namespace) -> int:
    payload = inspect_schematic(args.schematic, args.only_text, args.margin)
    if args.format != "text":
        print_json(payload)
        return 0

    score = payload["score"]
    print(f"Score: total {score['total']} (collisions {score['collision_count']}, wire corners {score['wire_corner_count']})")
    print(f"Wire length: {payload['wire_length']['total_mm']} mm across {payload['wire_length']['segment_count']} segments")
    print(f"Collisions: {len(payload['collisions'])}")
    for collision in payload["collisions"]:
        owner = collision["kind"]
        if collision["owner_ref"] and collision["owner_key"]:
            owner = f"{collision['owner_ref']}.{collision['owner_key']}"
        print(f"- {collision['text']} [{owner}]")
    print(f"Symbol-wire conflicts: {len(payload['symbol_wire_conflicts'])}")
    return 0


def cmd_sch_erc(args: argparse.Namespace) -> int:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = command_result(
        "sch erc",
        [
            kicad_cli(),
            "sch",
            "erc",
            "--exit-code-violations",
            "--output",
            str(args.output),
            str(args.schematic),
        ],
    )
    if args.format == "text":
        print(f"ERC: exit={result['returncode']} report={args.output}")
        if result["stderr"]:
            sys.stderr.write(result["stderr"])
    else:
        print_json({"erc": result, "report": str(args.output)})
    return result["returncode"]


def cmd_sch_netlist(args: argparse.Namespace) -> int:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = command_result(
        "sch netlist",
        [
            kicad_cli(),
            "sch",
            "export",
            "netlist",
            "--output",
            str(args.output),
            str(args.schematic),
        ],
    )
    if args.format == "text":
        print(f"Netlist: exit={result['returncode']} output={args.output}")
        if result["stderr"]:
            sys.stderr.write(result["stderr"])
    else:
        print_json({"netlist": result, "output": str(args.output)})
    return result["returncode"]


def _run_erc(schematic: Path, report: Path) -> dict[str, Any]:
    report.parent.mkdir(parents=True, exist_ok=True)
    return command_result(
        "sch erc",
        [
            kicad_cli(),
            "sch",
            "erc",
            "--exit-code-violations",
            "--output",
            str(report),
            str(schematic),
        ],
    )


def _run_netlist(schematic: Path, out: Path) -> dict[str, Any]:
    out.parent.mkdir(parents=True, exist_ok=True)
    return command_result(
        "sch netlist",
        [
            kicad_cli(),
            "sch",
            "export",
            "netlist",
            "--output",
            str(out),
            str(schematic),
        ],
    )


def _erc_violation_count(report_path: Path) -> int:
    """Count ERC violations from a kicad-cli erc text report."""
    try:
        text = report_path.read_text()
    except OSError:
        return 0
    # kicad-cli emits a "** Found N ERC violations **" line at the end.
    m = re.search(r"Found\s+(\d+)\s+ERC\s+violations", text)
    if m:
        return int(m.group(1))
    # Fallback: count lines that look like violations.
    return len(re.findall(r"^\[.*\]:", text, re.MULTILINE))


def cmd_sch_validate(args: argparse.Namespace) -> int:
    schematic = args.schematic
    sheet = args.sheet if args.sheet is not None else schematic

    # --- Save baseline mode ---
    if args.save_baseline is not None:
        base = Path(args.save_baseline)
        base.mkdir(parents=True, exist_ok=True)
        erc_report = base / "erc.rpt"
        netlist_out = base / "netlist.net"
        inspect_out = base / "inspect.json"

        erc = _run_erc(schematic, erc_report)
        netlist = _run_netlist(schematic, netlist_out)
        try:
            inspect = inspect_schematic(sheet, args.only_text, args.margin)
            inspect_rc = 0
        except subprocess.CalledProcessError as exc:
            inspect = {"stderr": exc.stderr}
            inspect_rc = exc.returncode
        inspect_out.write_text(json.dumps(inspect, ensure_ascii=False, indent=2))

        payload = {
            "mode": "save-baseline",
            "baseline_dir": str(base),
            "erc": {**erc, "report": str(erc_report)},
            "netlist": {**netlist, "output": str(netlist_out)},
            "inspect": {"output": str(inspect_out), "returncode": inspect_rc},
        }
        if args.format == "text":
            print(f"Baseline saved to {base}")
            print(f"  erc:     {erc_report} (exit={erc['returncode']})")
            print(f"  netlist: {netlist_out} (exit={netlist['returncode']})")
            print(f"  inspect: {inspect_out} (exit={inspect_rc})")
        else:
            print_json(payload)
        return 0

    # --- Baseline-diff mode ---
    if args.baseline is not None:
        base = Path(args.baseline)
        if not base.is_dir():
            sys.stderr.write(f"baseline dir not found: {base}\n")
            return 2
        base_erc = base / "erc.rpt"
        base_netlist = base / "netlist.net"
        base_inspect = base / "inspect.json"

        # Produce current state in tmp.
        with tempfile.TemporaryDirectory(prefix="kicad-validate-cur-") as tmp:
            tmp_path = Path(tmp)
            cur_erc_report = tmp_path / "erc.rpt"
            cur_netlist_out = tmp_path / "netlist.net"

            erc = _run_erc(schematic, cur_erc_report)
            netlist = _run_netlist(schematic, cur_netlist_out)
            try:
                cur_inspect = inspect_schematic(sheet, args.only_text, args.margin)
                cur_inspect_rc = 0
            except subprocess.CalledProcessError as exc:
                cur_inspect = {"stderr": exc.stderr}
                cur_inspect_rc = exc.returncode

            base_v = _erc_violation_count(base_erc) if base_erc.exists() else 0
            cur_v = _erc_violation_count(cur_erc_report)
            erc_delta = cur_v - base_v

            net_diff: dict[str, Any]
            try:
                a = sch_netlist.parse_netlist(str(base_netlist)) if base_netlist.exists() else {}
                b = sch_netlist.parse_netlist(str(cur_netlist_out))
                net_diff = sch_netlist.diff_netlists(a, b)
            except Exception as exc:  # noqa: BLE001
                net_diff = {"error": str(exc)}

            base_inspect_data: dict[str, Any] = {}
            if base_inspect.exists():
                try:
                    base_inspect_data = json.loads(base_inspect.read_text())
                except Exception:  # noqa: BLE001
                    base_inspect_data = {}

            inspect_diff = {
                "baseline_score": base_inspect_data.get("score"),
                "current_score": cur_inspect.get("score") if isinstance(cur_inspect, dict) else None,
            }

            # Determine regression: new ERC violations or netlist node/net loss.
            regression = False
            reasons: list[str] = []
            if erc_delta > 0:
                regression = True
                reasons.append(f"erc: +{erc_delta} new violations ({base_v} -> {cur_v})")
            if isinstance(net_diff, dict) and "error" not in net_diff:
                if net_diff.get("removed_nets"):
                    regression = True
                    reasons.append(f"netlist: removed nets {net_diff['removed_nets']}")
                for name, ch in net_diff.get("changed_nets", {}).items():
                    if ch.get("removed_nodes"):
                        regression = True
                        reasons.append(
                            f"netlist: net {name!r} lost nodes {[(n['ref'], n['pin']) for n in ch['removed_nodes']]}"
                        )

            payload = {
                "mode": "baseline-diff",
                "baseline_dir": str(base),
                "erc": {
                    "baseline_violations": base_v,
                    "current_violations": cur_v,
                    "delta": erc_delta,
                    "current_returncode": erc["returncode"],
                },
                "netlist_diff": net_diff,
                "inspect": inspect_diff,
                "regression": regression,
                "reasons": reasons,
            }
            if args.format != "text":
                print_json(payload)
            else:
                print(f"Baseline: {base}")
                print(f"  ERC: {base_v} -> {cur_v} (delta {erc_delta:+d})")
                if isinstance(net_diff, dict) and "error" not in net_diff:
                    print(f"  Nets added: {net_diff.get('added_nets', [])}")
                    print(f"  Nets removed: {net_diff.get('removed_nets', [])}")
                    print(f"  Nets changed: {len(net_diff.get('changed_nets', {}))}")
                else:
                    print(f"  Netlist diff error: {net_diff.get('error')}")
                if regression:
                    print("REGRESSION:")
                    for r in reasons:
                        print(f"  - {r}")
                else:
                    print("OK (no regressions detected)")
            return 1 if regression else 0

    # --- Default mode: same as before ---
    if args.sheet is None:
        sys.stderr.write("validate: --sheet is required (or use --save-baseline / --baseline)\n")
        return 2
    erc_report = args.erc_report
    netlist_out = args.netlist_out
    erc_report.parent.mkdir(parents=True, exist_ok=True)
    netlist_out.parent.mkdir(parents=True, exist_ok=True)

    erc = _run_erc(schematic, erc_report)
    netlist = _run_netlist(schematic, netlist_out)

    inspect: dict[str, Any]
    try:
        inspect = inspect_schematic(args.sheet, args.only_text, args.margin)
        inspect_returncode = 0
    except subprocess.CalledProcessError as exc:
        inspect = {"stderr": exc.stderr}
        inspect_returncode = exc.returncode

    payload = {
        "erc": {**erc, "report": str(erc_report)},
        "netlist": {**netlist, "output": str(netlist_out)},
        "inspect": inspect,
    }
    if args.format != "text":
        print_json(payload)
    else:
        print(f"ERC: exit={erc['returncode']} report={erc_report}")
        print(f"Netlist: exit={netlist['returncode']} output={netlist_out}")
        if inspect_returncode == 0:
            score = inspect["score"]
            print(f"Inspect: total {score['total']} (collisions {score['collision_count']}, wire corners {score['wire_corner_count']})")
        else:
            print(f"Inspect: exit={inspect_returncode}")

    return 0 if erc["returncode"] == netlist["returncode"] == inspect_returncode == 0 else 1


DEFAULT_PCB_RENDER_LAYERS = (
    "F.Cu,B.Cu,F.SilkS,B.SilkS,F.CrtYd,B.CrtYd,Edge.Cuts"
)


def render_pcb_region(
    board: Path,
    bbox_mm: tuple[float, float, float, float],
    layers: str,
    out: Path,
) -> Path:
    x1, y1, x2, y2 = bbox_mm
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1
    x, y, w, h = x1, y1, x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        raise ValueError(f"empty bbox: {bbox_mm}")

    rsvg = subprocess.run(["which", "rsvg-convert"], capture_output=True, text=True)
    if rsvg.returncode != 0:
        raise RuntimeError("rsvg-convert is required for render-region; install librsvg")

    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kicad-tool-pcb-region-") as tmpdir:
        svg_dir = Path(tmpdir)
        svg_path = svg_dir / f"{board.stem}.svg"
        run_checked([
            kicad_cli(),
            "pcb", "export", "svg",
            "--output", str(svg_path),
            "--layers", layers,
            "--mode-single",
            "--page-size-mode", "0",
            "--exclude-drawing-sheet",
            str(board),
        ])
        if not svg_path.exists():
            svgs = sorted(svg_dir.glob("*.svg"))
            if not svgs:
                raise FileNotFoundError(f"no SVG exported into {svg_dir}")
            svg_path = svgs[0]
        original = svg_path.read_text(encoding="utf-8")
        cropped = _rewrite_svg_viewbox(original, x, y, w, h)
        cropped_path = svg_dir / "region.svg"
        cropped_path.write_text(cropped, encoding="utf-8")

        px_w = max(1, int(round(w * PX_PER_MM)))
        run_checked([
            "rsvg-convert",
            "-w", str(px_w),
            "-o", str(out),
            str(cropped_path),
        ])
    return out


def cmd_pcb_render_region(args: argparse.Namespace) -> int:
    layers = args.layers if args.layers else DEFAULT_PCB_RENDER_LAYERS
    try:
        png = render_pcb_region(args.board, args.bbox, layers, args.output)
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr or "")
        return exc.returncode
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    print(png)
    return 0


# ---------------------------------------------------------------------------
# pcb query handlers
# ---------------------------------------------------------------------------


def cmd_pcb_query_list(args: argparse.Namespace) -> int:
    return _emit_query(args, pcb_query.query_list(args.board, args.element))


def cmd_pcb_query_footprint(args: argparse.Namespace) -> int:
    return _emit_query(args, pcb_query.query_footprint(args.board, args.ref))


def cmd_pcb_query_pad(args: argparse.Namespace) -> int:
    return _emit_query(args, pcb_query.query_pad(args.board, args.ref_pad))


def cmd_pcb_query_net(args: argparse.Namespace) -> int:
    return _emit_query(args, pcb_query.query_net(args.board, args.target))


def cmd_pcb_query_region(args: argparse.Namespace) -> int:
    return _emit_query(args, pcb_query.query_region(args.board, args.bbox))


def cmd_pcb_drc(args: argparse.Namespace) -> int:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    command = [kicad_cli(), "pcb", "drc"]
    if args.schematic_parity:
        command.append("--schematic-parity")
    command.extend(["--output", str(args.output), str(args.board)])
    result = command_result("pcb drc", command)
    if args.format == "text":
        print(f"DRC: exit={result['returncode']} report={args.output}")
        if result["stderr"]:
            sys.stderr.write(result["stderr"])
    else:
        print_json({"drc": result, "report": str(args.output)})
    return result["returncode"]


# ---------------------------------------------------------------------------
# pcb edit handlers
# ---------------------------------------------------------------------------


def cmd_pcb_edit_footprint_move(args: argparse.Namespace) -> int:
    res = pcb_edit.move_footprint(
        args.board,
        args.ref,
        args.xy,
        rotation=args.rotation,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


def cmd_pcb_edit_footprint_move_property(args: argparse.Namespace) -> int:
    res = pcb_edit.move_footprint_property(
        args.board,
        args.ref,
        args.key,
        args.xy,
        rotation=args.rotation,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


def cmd_pcb_edit_footprint_set_property(args: argparse.Namespace) -> int:
    res = pcb_edit.set_footprint_property(
        args.board,
        args.ref,
        args.key,
        args.value,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


def cmd_pcb_edit_footprint_move_layer(args: argparse.Namespace) -> int:
    res = pcb_edit.move_footprint_layer(
        args.board,
        args.ref,
        args.side,
        at=args.at,
        rotation=args.rotation,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


def cmd_pcb_edit_footprint_delete(args: argparse.Namespace) -> int:
    res = pcb_edit.delete_footprint(
        args.board,
        args.ref,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


# ---------------------------------------------------------------------------
# pcb zone query / edit handlers
# ---------------------------------------------------------------------------


def _zone_selector_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """Pull --uuid / --name / --net / --layer selector flags off the parsed
    args. Performs the same mutex validation that `_locate_zone` does, so
    we surface argparse-style errors before opening the file.
    """
    uuid = getattr(args, "uuid", None)
    name = getattr(args, "name", None)
    net = getattr(args, "net", None)
    layer = getattr(args, "layer", None)
    active = sum(
        1
        for x in (
            uuid is not None,
            name is not None,
            net is not None or layer is not None,
        )
        if x
    )
    if active != 1:
        raise ValueError(
            "exactly one of --uuid, --name, or (--net + --layer) must be specified"
        )
    if (net is not None) != (layer is not None):
        raise ValueError("--net and --layer must be used together")
    return {"uuid": uuid, "name": name, "net": net, "layer": layer}


def cmd_pcb_query_zone(args: argparse.Namespace) -> int:
    kwargs = _zone_selector_kwargs(args)
    return _emit_query(args, pcb_query.query_zone(args.board, **kwargs))


def _parse_polygon_points(raw: list[str]) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for token in raw:
        x, y = parse_xy(token)
        pts.append((x, y))
    if len(pts) < 3:
        raise ValueError(
            f"polygon needs at least 3 points, got {len(pts)}"
        )
    return pts


def cmd_pcb_edit_zone_set_polygon(args: argparse.Namespace) -> int:
    kwargs = _zone_selector_kwargs(args)
    points = _parse_polygon_points(args.points)
    res = pcb_edit.set_zone_polygon(
        args.board,
        points=points,
        dry_run=args.dry_run,
        **kwargs,
    )
    return _emit_edit(args, res)


def cmd_pcb_edit_zone_add(args: argparse.Namespace) -> int:
    points = _parse_polygon_points(args.points)
    res = pcb_edit.add_zone(
        args.board,
        args.net,
        args.layer,
        points,
        copy_settings_from_uuid=args.copy_settings_from_uuid,
        name=args.name,
        priority=args.priority,
        clearance=args.clearance,
        min_thickness=args.min_thickness,
        thermal_gap=args.thermal_gap,
        thermal_bridge_width=args.thermal_bridge_width,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


def cmd_pcb_edit_zone_delete(args: argparse.Namespace) -> int:
    kwargs = _zone_selector_kwargs(args)
    res = pcb_edit.delete_zone(
        args.board,
        dry_run=args.dry_run,
        **kwargs,
    )
    return _emit_edit(args, res)


def cmd_pcb_edit_zone_set_property(args: argparse.Namespace) -> int:
    uuid = getattr(args, "uuid", None)
    name = getattr(args, "name", None)
    if (uuid is None) == (name is None):
        raise ValueError("exactly one of --uuid or --name must be specified")
    key = args.key
    raw_value = args.value
    if key == "priority":
        value: Any = int(raw_value)
    elif key == "name":
        value = str(raw_value)
    else:
        value = float(raw_value)
    res = pcb_edit.set_zone_property(
        args.board,
        uuid=uuid,
        name=name,
        key=key,
        value=value,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


# ---------------------------------------------------------------------------
# pcb sync / pcb validate
# ---------------------------------------------------------------------------


def _export_sch_netlist(schematic: Path, out: Path) -> dict[str, Any]:
    out.parent.mkdir(parents=True, exist_ok=True)
    return command_result(
        "sch netlist",
        [
            kicad_cli(),
            "sch", "export", "netlist",
            "--output", str(out),
            str(schematic),
        ],
    )


def cmd_pcb_sync(args: argparse.Namespace) -> int:
    netlist_out = Path("tmp/pcb-sync-netlist.net")
    nl = _export_sch_netlist(args.schematic, netlist_out)
    if nl["returncode"] != 0:
        sys.stderr.write(nl.get("stderr") or "")
        return nl["returncode"]

    res = pcb_edit.sync_from_schematic(
        args.board,
        netlist_out,
        project_dir=args.board.parent,
        output_path=args.output,
        dry_run=args.dry_run,
    )

    if args.format != "text":
        payload = {k: v for k, v in res.items() if k != "diff"}
        print_json(payload)
        if args.dry_run and res.get("diff"):
            sys.stderr.write(res["diff"])
    else:
        print(f"action: {res['action']}")
        print(f"changed: {res['changed']}  wrote: {res['wrote']}  target: {res['target']}")
        d = res["details"]
        print(
            f"schematic_refs={d['schematic_refs']} existing={d['existing_refs']} "
            f"added={len(d['added'])} pad_net_changes={len(d['pad_net_changes'])} "
            f"pad_net_added={len(d['pad_net_added'])}"
        )
        print(
            f"swapped={len(d.get('swapped', []))} "
            f"swap_skipped={len(d.get('swap_skipped', []))} "
            f"refreshed={len(d.get('refreshed', []))} "
            f"refresh_skipped={len(d.get('refresh_skipped', []))}"
        )
        if d.get("swapped"):
            for s in d["swapped"]:
                print(f"  swap {s['ref']}: {s['old_lib_id']} -> {s['new_lib_id']}")
        if d.get("swap_skipped"):
            for s in d["swap_skipped"]:
                print(f"  swap-skip {s['ref']} ({s['reason']})")
        if d.get("refreshed"):
            for r in d["refreshed"]:
                preserved = r.get("preserved_user_properties") or []
                tail = f" preserved={preserved}" if preserved else ""
                print(f"  refresh {r['ref']}: {r['lib_id']}{tail}")
        if d.get("refresh_skipped"):
            for r in d["refresh_skipped"]:
                print(f"  refresh-skip {r['ref']} ({r['reason']})")
        if d["unresolved"]:
            print(f"unresolved: {d['unresolved']}")
        if d["skipped_no_footprint"]:
            print(f"skipped (no footprint assigned): {d['skipped_no_footprint']}")
        print(
            f"parity.clean={d['parity']['clean']} "
            f"missing_on_board={d['parity']['missing_on_board']} "
            f"extra_on_board={d['parity']['extra_on_board']}"
        )
        if d["orphaned_nets"]:
            print(f"orphaned_nets: {d['orphaned_nets']}")
        if args.dry_run and res.get("diff"):
            sys.stdout.write(res["diff"])
    parity_clean = res["details"]["parity"]["clean"]
    return 0 if parity_clean else 1


# --- pcb validate ---


def _drc_violation_count(report_path: Path) -> int | None:
    """Parse a kicad-cli pcb drc text report. Returns None if the report is
    missing or unparseable."""
    if not report_path.exists():
        return None
    try:
        text = report_path.read_text()
    except OSError:
        return None
    # KiCad 10 canonical combined form:
    #   "Found N violations, M unconnected items"
    m_combined = re.search(
        r"Found\s+(\d+)\s+violations?,\s*(\d+)\s+unconnected\s+items?",
        text,
    )
    if m_combined:
        return int(m_combined.group(1)) + int(m_combined.group(2))
    # Older split form: "Found N DRC violations" + "Found N unconnected pads/items".
    found_any = False
    total = 0
    m = re.search(r"Found\s+(\d+)\s+DRC\s+violations?", text)
    if m:
        found_any = True
        total += int(m.group(1))
    m2 = re.search(r"Found\s+(\d+)\s+unconnected", text)
    if m2:
        found_any = True
        total += int(m2.group(1))
    if found_any:
        return total
    # Fallback: treat the report as parseable if it has any "Found" summary line.
    if "Found" in text:
        return 0
    return None


def _run_pcb_drc(board: Path, report: Path) -> dict[str, Any]:
    """Run kicad-cli pcb drc; tolerate process crashes by reporting them
    explicitly rather than masking as a clean run."""
    report.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            kicad_cli(), "pcb", "drc",
            "--exit-code-violations",
            "--output", str(report),
            str(board),
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    violations = _drc_violation_count(report)
    # Crashed iff killed by a signal, or the report is missing/unparseable.
    # A nonzero returncode alone is expected with --exit-code-violations when
    # violations > 0, so we rely on the parsed count for the clean/violations
    # split rather than the exit code.
    crashed = proc.returncode < 0 or violations is None
    status = "crashed" if crashed else ("violations" if violations > 0 else "clean")
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "report": str(report),
        "report_exists": report.exists(),
        "violations": violations,
        "status": status,
    }


def _board_footprint_refs(board: Path) -> dict[str, str]:
    """Return ``{ref: lib_id}`` for the board, by parsing its top-level
    ``(footprint ...)`` blocks."""
    text = Path(board).read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for m in re.finditer(
        r'^\t\(footprint\s+"([^"]+)"', text, flags=re.MULTILINE
    ):
        # Find this block end and pull out the Reference property.
        start = m.start()
        # Manually locate matching paren — reuse the helper from pcb_edit.
        end = pcb_edit._find_block_end(text, start + 1)
        block = text[start:end]
        ref_m = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', block)
        if ref_m:
            out[ref_m.group(1)] = m.group(1)
    return out


def _parity_check(board: Path, sch_netlist: Path) -> dict[str, Any]:
    components = pcb_netlist.parse_components(sch_netlist)
    sch_refs = {ref: c["footprint"] for ref, c in components.items()}
    board_refs = _board_footprint_refs(board)

    refs_missing_on_board = sorted(set(sch_refs) - set(board_refs))
    refs_extra_on_board = sorted(set(board_refs) - set(sch_refs))
    fp_mismatches: list[dict[str, str]] = []
    for ref in sorted(set(sch_refs) & set(board_refs)):
        sf = sch_refs[ref]
        bf = board_refs[ref]
        if sf and sf != bf:
            fp_mismatches.append({
                "ref": ref,
                "schematic_footprint": sf,
                "board_footprint": bf,
            })

    clean = (
        not refs_missing_on_board
        and not refs_extra_on_board
        and not fp_mismatches
    )
    return {
        "clean": clean,
        "schematic_refs": len(sch_refs),
        "board_refs": len(board_refs),
        "refs_missing_on_board": refs_missing_on_board,
        "refs_extra_on_board": refs_extra_on_board,
        "footprint_mismatches": fp_mismatches,
    }


def cmd_pcb_validate(args: argparse.Namespace) -> int:
    board = args.board
    schematic = args.schematic

    # --- Save baseline mode ---
    if args.save_baseline is not None:
        base = Path(args.save_baseline)
        base.mkdir(parents=True, exist_ok=True)
        drc_report = base / "drc.rpt"
        netlist_out = base / "sch-netlist.net"
        parity_out = base / "parity.json"

        nl = _export_sch_netlist(schematic, netlist_out)
        drc = _run_pcb_drc(board, drc_report)
        if nl["returncode"] == 0:
            parity = _parity_check(board, netlist_out)
        else:
            parity = {"clean": False, "error": "schematic netlist export failed"}
        parity_out.write_text(json.dumps(parity, ensure_ascii=False, indent=2))

        payload = {
            "mode": "save-baseline",
            "baseline_dir": str(base),
            "drc": drc,
            "netlist": {**nl, "output": str(netlist_out)},
            "parity": parity,
        }
        if args.format == "text":
            print(f"Baseline saved to {base}")
            print(f"  drc:    {drc_report} (status={drc['status']}, violations={drc['violations']})")
            print(f"  parity: {parity_out} (clean={parity.get('clean')})")
        else:
            print_json(payload)
        return 0

    # --- Baseline diff mode ---
    if args.baseline is not None:
        base = Path(args.baseline)
        if not base.is_dir():
            sys.stderr.write(f"baseline dir not found: {base}\n")
            return 2
        with tempfile.TemporaryDirectory(prefix="kicad-pcb-validate-cur-") as tmp:
            tmp_path = Path(tmp)
            cur_drc = tmp_path / "drc.rpt"
            cur_netlist = tmp_path / "sch-netlist.net"

            nl = _export_sch_netlist(schematic, cur_netlist)
            drc = _run_pcb_drc(board, cur_drc)
            if nl["returncode"] == 0:
                parity = _parity_check(board, cur_netlist)
            else:
                parity = {"clean": False, "error": "schematic netlist export failed"}

            base_drc = _drc_violation_count(base / "drc.rpt")
            base_parity_path = base / "parity.json"
            base_parity: dict[str, Any] = {}
            if base_parity_path.exists():
                try:
                    base_parity = json.loads(base_parity_path.read_text())
                except Exception:  # noqa: BLE001
                    base_parity = {}

            cur_v = drc["violations"] if drc["violations"] is not None else 0
            base_v = base_drc if base_drc is not None else 0
            erc_delta = cur_v - base_v
            regression = False
            reasons: list[str] = []
            if drc["status"] == "crashed":
                regression = True
                reasons.append(f"drc crashed (returncode={drc['returncode']})")
            elif erc_delta > 0:
                regression = True
                reasons.append(f"drc: +{erc_delta} new violations ({base_v} -> {cur_v})")
            if not parity.get("clean"):
                regression = True
                reasons.append(
                    f"parity not clean: missing={parity.get('refs_missing_on_board')} "
                    f"extra={parity.get('refs_extra_on_board')} "
                    f"mismatch={parity.get('footprint_mismatches')}"
                )

            payload = {
                "mode": "baseline-diff",
                "baseline_dir": str(base),
                "drc": {
                    "baseline_violations": base_v,
                    "current_violations": cur_v,
                    "delta": erc_delta,
                    "current_status": drc["status"],
                    "current_returncode": drc["returncode"],
                },
                "parity": parity,
                "baseline_parity": base_parity,
                "regression": regression,
                "reasons": reasons,
            }
            if args.format == "text":
                print(f"Baseline: {base}")
                print(f"  DRC: {base_v} -> {cur_v} (delta {erc_delta:+d}) status={drc['status']}")
                print(f"  Parity clean: {parity.get('clean')}")
                if regression:
                    print("REGRESSION:")
                    for r in reasons:
                        print(f"  - {r}")
                else:
                    print("OK")
            else:
                print_json(payload)
            return 1 if regression else 0

    # --- Default mode ---
    drc_report = args.drc_report
    netlist_out = args.netlist_out
    nl = _export_sch_netlist(schematic, netlist_out)
    drc = _run_pcb_drc(board, drc_report)
    if nl["returncode"] == 0:
        parity = _parity_check(board, netlist_out)
    else:
        parity = {"clean": False, "error": "schematic netlist export failed"}

    payload = {
        "drc": drc,
        "netlist": {**nl, "output": str(netlist_out)},
        "parity": parity,
    }
    if args.format == "text":
        print(f"DRC: status={drc['status']} violations={drc['violations']} report={drc_report}")
        if drc["status"] == "crashed":
            print(f"  DRC crashed; returncode={drc['returncode']}")
        print(f"Parity: clean={parity.get('clean')}")
        if not parity.get("clean"):
            print(f"  missing_on_board={parity.get('refs_missing_on_board')}")
            print(f"  extra_on_board={parity.get('refs_extra_on_board')}")
            print(f"  footprint_mismatches={parity.get('footprint_mismatches')}")
    else:
        print_json(payload)

    bad = (drc["status"] != "clean") or (not parity.get("clean"))
    return 1 if bad else 0


# ---------------------------------------------------------------------------
# sch query handlers
# ---------------------------------------------------------------------------


def _emit_query(args: argparse.Namespace, payload: dict[str, Any]) -> int:
    if getattr(args, "format", "text") == "json":
        print_json(payload)
        return 0 if payload.get("found", True) else 1
    if not payload.get("found", True):
        print(f"not found: {payload.get('query') or payload.get('ref') or payload.get('name') or ''}".rstrip())
        return 1
    _format_query_text(payload)
    return 0


def _format_query_text(payload: dict[str, Any]) -> None:
    """Emit a YAML-like text rendering of a query payload.

    Same content as ``--format json``, just laid out for human reading: scalars
    as ``key: value``, dicts indented, lists as ``- `` blocks. No truncation,
    no JSON fallback — text and JSON carry identical information.
    """
    for line in _format_value_lines(payload, indent=0, top_level=True):
        print(line)


def _format_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _format_value_lines(value: Any, indent: int, top_level: bool = False) -> list[str]:
    pad = "  " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        if not value:
            return [f"{pad}{{}}"] if not top_level else []
        for k, v in value.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                lines.append(f"{pad}{k}: {_format_scalar(v)}")
            elif isinstance(v, list):
                if not v:
                    lines.append(f"{pad}{k}: []")
                elif all(isinstance(x, (str, int, float, bool)) or x is None for x in v):
                    lines.append(f"{pad}{k}: [{', '.join(_format_scalar(x) for x in v)}]")
                else:
                    lines.append(f"{pad}{k}:")
                    for item in v:
                        lines.extend(_format_list_item_lines(item, indent + 1))
            elif isinstance(v, dict):
                if not v:
                    lines.append(f"{pad}{k}: {{}}")
                else:
                    lines.append(f"{pad}{k}:")
                    lines.extend(_format_value_lines(v, indent + 1))
            else:
                lines.append(f"{pad}{k}: {_format_scalar(v)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{pad}[]"]
        for item in value:
            lines.extend(_format_list_item_lines(item, indent))
        return lines
    return [f"{pad}{_format_scalar(value)}"]


def _format_list_item_lines(item: Any, indent: int) -> list[str]:
    pad = "  " * indent
    if isinstance(item, (str, int, float, bool)) or item is None:
        return [f"{pad}- {_format_scalar(item)}"]
    if isinstance(item, dict):
        if not item:
            return [f"{pad}- {{}}"]
        lines: list[str] = []
        first = True
        for k, v in item.items():
            prefix = f"{pad}- " if first else f"{pad}  "
            first = False
            if isinstance(v, (str, int, float, bool)) or v is None:
                lines.append(f"{prefix}{k}: {_format_scalar(v)}")
            elif isinstance(v, list):
                if not v:
                    lines.append(f"{prefix}{k}: []")
                elif all(isinstance(x, (str, int, float, bool)) or x is None for x in v):
                    lines.append(f"{prefix}{k}: [{', '.join(_format_scalar(x) for x in v)}]")
                else:
                    lines.append(f"{prefix}{k}:")
                    for sub in v:
                        lines.extend(_format_list_item_lines(sub, indent + 2))
            elif isinstance(v, dict):
                if not v:
                    lines.append(f"{prefix}{k}: {{}}")
                else:
                    lines.append(f"{prefix}{k}:")
                    lines.extend(_format_value_lines(v, indent + 2))
            else:
                lines.append(f"{prefix}{k}: {_format_scalar(v)}")
        return lines
    if isinstance(item, list):
        return [f"{pad}- "] + _format_value_lines(item, indent + 1)
    return [f"{pad}- {_format_scalar(item)}"]


def cmd_sch_query_symbol(args: argparse.Namespace) -> int:
    return _emit_query(args, sch_query.query_symbol(args.schematic, args.ref))


def cmd_sch_query_pin(args: argparse.Namespace) -> int:
    return _emit_query(
        args,
        sch_query.query_pin(
            args.schematic,
            args.ref_pin,
            netlist_path=args.netlist,
            auto_netlist=not getattr(args, "no_netlist", False),
        ),
    )


def cmd_sch_query_net(args: argparse.Namespace) -> int:
    target = args.target
    if "." in target:
        name = None
        pin = target
    else:
        name = target
        pin = None
    return _emit_query(
        args,
        sch_query.query_net(args.schematic, args.netlist, name=name, pin=pin),
    )


def cmd_sch_query_region(args: argparse.Namespace) -> int:
    return _emit_query(args, sch_query.query_region(args.schematic, args.bbox))


def cmd_sch_query_wire(args: argparse.Namespace) -> int:
    return _emit_query(
        args,
        sch_query.query_wire(args.schematic, uuid=args.uuid, at=args.at, through=args.through),
    )


def cmd_sch_query_label(args: argparse.Namespace) -> int:
    return _emit_query(args, sch_query.query_label(args.schematic, name=args.name, uuid=args.uuid))


def cmd_sch_query_lib_symbol(args: argparse.Namespace) -> int:
    return _emit_query(args, sch_query.query_lib_symbol(args.schematic, args.lib_id))


def cmd_sch_query_list(args: argparse.Namespace) -> int:
    return _emit_query(
        args,
        sch_query.query_list(
            args.schematic,
            args.element,
            netlist_path=args.netlist,
            auto_netlist=not getattr(args, "no_netlist", False),
        ),
    )


# ---------------------------------------------------------------------------
# sch edit handlers
# ---------------------------------------------------------------------------


def _emit_edit(args: argparse.Namespace, payload: dict[str, Any]) -> int:
    if args.format != "text":
        print_json(payload)
    else:
        print(f"action: {payload.get('action')}")
        print(f"changed: {payload.get('changed')}")
        details = payload.get("details") or {}
        if details:
            print("details:")
            print(json.dumps(details, ensure_ascii=False, indent=2))
        if args.dry_run:
            diff = payload.get("diff") or ""
            if diff:
                sys.stdout.write(diff)
    return 0


def cmd_sch_edit_symbol_move(args: argparse.Namespace) -> int:
    res = sch_edit.move_symbol(
        args.schematic,
        args.ref,
        args.xy,
        rotation=args.rotation,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


def cmd_sch_edit_symbol_move_property(args: argparse.Namespace) -> int:
    res = sch_edit.move_symbol_property(
        args.schematic,
        args.ref,
        args.key,
        args.xy,
        rotation=args.rotation,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


def cmd_sch_edit_symbol_add(args: argparse.Namespace) -> int:
    res = sch_edit.add_symbol(
        args.schematic,
        lib_id=args.lib_id,
        ref=args.ref,
        at=args.at,
        dry_run=args.dry_run,
        lib_file=args.lib_file,
    )
    return _emit_edit(args, res)


def cmd_sch_edit_symbol_add_pin(args: argparse.Namespace) -> int:
    x, y = args.at
    res = sch_edit.add_pin(
        args.schematic,
        lib_id=args.lib_id,
        number=args.number,
        name=args.name,
        at=(x, y, args.rotation),
        length=args.length,
        electrical_type=args.type,
        shape=args.shape,
        lib_file=args.lib_file,
        font_size=args.font_size,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


def cmd_sch_edit_symbol_delete(args: argparse.Namespace) -> int:
    res = sch_edit.delete_symbol(args.schematic, args.ref, dry_run=args.dry_run)
    return _emit_edit(args, res)


def cmd_sch_edit_symbol_set_property(args: argparse.Namespace) -> int:
    res = sch_edit.set_symbol_property(
        args.schematic,
        args.ref,
        args.key,
        args.value,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


def cmd_sch_edit_symbol_set_attribute(args: argparse.Namespace) -> int:
    res = sch_edit.set_symbol_attribute(
        args.schematic,
        args.ref,
        args.attribute,
        args.value,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


def cmd_sch_edit_wire_add(args: argparse.Namespace) -> int:
    res = sch_edit.add_wire(
        args.schematic,
        args.from_pt,
        args.to_pt,
        wire_type=args.type,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


def cmd_sch_edit_wire_delete(args: argparse.Namespace) -> int:
    res = sch_edit.delete_wire(
        args.schematic,
        uuid=args.uuid,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


def cmd_sch_edit_label_add(args: argparse.Namespace) -> int:
    res = sch_edit.add_label(
        args.schematic,
        args.kind,
        args.name,
        args.xy,
        rotation=args.rotation,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


def cmd_sch_edit_label_move(args: argparse.Namespace) -> int:
    res = sch_edit.move_label(
        args.schematic,
        args.uuid,
        args.xy,
        rotation=args.rotation,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


def cmd_sch_edit_label_delete(args: argparse.Namespace) -> int:
    res = sch_edit.delete_label(args.schematic, args.uuid, dry_run=args.dry_run)
    return _emit_edit(args, res)


def cmd_sch_edit_junction_add(args: argparse.Namespace) -> int:
    res = sch_edit.add_junction(args.schematic, args.xy, dry_run=args.dry_run)
    return _emit_edit(args, res)


def cmd_sch_edit_junction_delete(args: argparse.Namespace) -> int:
    res = sch_edit.delete_junction(
        args.schematic,
        uuid=args.uuid,
        dry_run=args.dry_run,
    )
    return _emit_edit(args, res)


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------


def _add_query_subparsers(sch_commands) -> None:
    query = sch_commands.add_parser("query", help="read-only schematic queries")
    qsub = query.add_subparsers(dest="query_element", required=True)

    p = qsub.add_parser("symbol", help="query a symbol by reference")
    p.add_argument("schematic", type=Path)
    p.add_argument("ref", help="symbol reference, e.g. U1")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_query_symbol)

    p = qsub.add_parser("pin", help="query a pin (REF.PIN)")
    p.add_argument("schematic", type=Path)
    p.add_argument("ref_pin", help="REF.PIN, e.g. U1.F3")
    p.add_argument("--netlist", type=Path, default=None,
                   help="explicit netlist; auto-generated from .kicad_pro if omitted")
    p.add_argument("--no-netlist", action="store_true",
                   help="skip netlist lookup (no auto-generation)")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_query_pin)

    p = qsub.add_parser("net", help="query a net by name or REF.PIN")
    p.add_argument("schematic", type=Path)
    p.add_argument("target", help="net name or REF.PIN (dispatched by '.')")
    p.add_argument("--netlist", type=Path, default=None,
                   help="explicit netlist; auto-generated from .kicad_pro if omitted")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_query_net)

    p = qsub.add_parser("region", help="query elements in a bbox")
    p.add_argument("schematic", type=Path)
    p.add_argument("bbox", type=parse_bbox, help="X1,Y1,X2,Y2")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_query_region)

    p = qsub.add_parser("wire", help="query wire segments")
    p.add_argument("schematic", type=Path)
    p.add_argument("--uuid")
    p.add_argument("--at", type=parse_xy, help="endpoint X,Y")
    p.add_argument("--through", type=parse_xy, help="pass-through X,Y")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_query_wire)

    p = qsub.add_parser("label", help="query labels")
    p.add_argument("schematic", type=Path)
    p.add_argument("--name")
    p.add_argument("--uuid")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_query_label)

    p = qsub.add_parser("lib-symbol", help="query a library symbol's pins")
    p.add_argument("schematic", type=Path)
    p.add_argument("lib_id", help='e.g. "cupwarmer:PLACEHOLDER_1"')
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_query_lib_symbol)

    p = qsub.add_parser("list", help="list elements")
    p.add_argument("schematic", type=Path)
    p.add_argument("element", choices=["symbols", "labels", "wires", "junctions", "nets"])
    p.add_argument("--netlist", type=Path, default=None,
                   help="explicit netlist (for 'nets'); auto-generated if omitted")
    p.add_argument("--no-netlist", action="store_true",
                   help="skip netlist lookup (no auto-generation)")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_query_list)


def _add_edit_subparsers(sch_commands) -> None:
    edit = sch_commands.add_parser("edit", help="schematic structural edits")
    esub = edit.add_subparsers(dest="edit_element", required=True)

    # symbol
    sym = esub.add_parser("symbol", help="edit a symbol")
    sym_act = sym.add_subparsers(dest="edit_action", required=True)
    p = sym_act.add_parser("move", help="move a symbol")
    p.add_argument("schematic", type=Path)
    p.add_argument("ref", help="symbol reference, e.g. R5")
    p.add_argument("xy", type=parse_xy, help="destination X,Y")
    p.add_argument("--rotation", type=float, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_edit_symbol_move)

    p = sym_act.add_parser(
        "move-property",
        help="move a single symbol property placement (Reference/Value/etc)",
    )
    p.add_argument("schematic", type=Path)
    p.add_argument("ref", help="symbol reference, e.g. C4")
    p.add_argument(
        "key",
        help="property key (standard built-ins only): Reference|Value|Footprint|Datasheet|Description",
    )
    p.add_argument("xy", type=parse_xy, help="destination X,Y")
    p.add_argument("--rotation", type=float, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_edit_symbol_move_property)

    p = sym_act.add_parser(
        "add",
        help="add a new symbol instance by cloning an existing same-lib_id sibling",
    )
    p.add_argument("schematic", type=Path)
    p.add_argument("lib_id", help='e.g. "Device:C"')
    p.add_argument("ref", help="new reference, e.g. C42")
    p.add_argument("at", type=parse_xy, help="X,Y in schematic coords")
    p.add_argument(
        "--lib-file", dest="lib_file", type=Path, default=None,
        help="external .kicad_sym to import lib_id from if not already embedded",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.set_defaults(func=cmd_sch_edit_symbol_add)

    p = sym_act.add_parser(
        "add-pin",
        help="add a pin to a symbol (updates embedded lib_symbols + standalone .kicad_sym)",
    )
    p.add_argument("schematic", type=Path)
    p.add_argument("lib_id", help='e.g. "cupwarmer:PLACEHOLDER_1"')
    p.add_argument("number", help="pin number (string)")
    p.add_argument("name", help="pin name")
    p.add_argument("at", type=parse_xy, help="X,Y in lib-local coords")
    p.add_argument("--length", type=float, required=True)
    p.add_argument(
        "--type", required=True,
        choices=sorted(sch_edit._PIN_ELECTRICAL_TYPES),
    )
    p.add_argument("--rotation", type=float, default=0.0)
    p.add_argument(
        "--shape", default="line",
        choices=sorted(sch_edit._PIN_SHAPES),
    )
    p.add_argument(
        "--lib-file", dest="lib_file", type=Path,
        default=Path("hardware/kicad/cupwarmer-hw/cupwarmer.kicad_sym"),
    )
    p.add_argument("--font-size", dest="font_size", type=float, default=1.27)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_edit_symbol_add_pin)

    p = sym_act.add_parser("delete", help="delete a symbol by reference")
    p.add_argument("schematic", type=Path)
    p.add_argument("ref", help="symbol reference, e.g. R5")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_edit_symbol_delete)

    p = sym_act.add_parser(
        "set-property",
        help="set a symbol property value (any key, including user-defined like MPN/LCSC)",
    )
    p.add_argument("schematic", type=Path)
    p.add_argument("ref", help="symbol reference, e.g. R12")
    p.add_argument("key", help='property key, e.g. "Value", "MPN", "LCSC"')
    p.add_argument("value", help="new property value (empty string allowed)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_edit_symbol_set_property)

    p = sym_act.add_parser(
        "set-attribute",
        help="set a symbol boolean attribute (in_bom/on_board)",
    )
    p.add_argument("schematic", type=Path)
    p.add_argument("ref", help="symbol reference, e.g. F1")
    p.add_argument("attribute", choices=["in_bom", "on_board"])
    p.add_argument("value", choices=["yes", "no"])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_edit_symbol_set_attribute)

    # wire
    wire = esub.add_parser("wire", help="edit wires")
    wire_act = wire.add_subparsers(dest="edit_action", required=True)
    p = wire_act.add_parser("add", help="add a wire")
    p.add_argument("schematic", type=Path)
    p.add_argument("from_pt", type=parse_xy, metavar="FROM", help="X1,Y1")
    p.add_argument("to_pt", type=parse_xy, metavar="TO", help="X2,Y2")
    p.add_argument("--type", choices=["solid", "default"], default="default")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_edit_wire_add)

    p = wire_act.add_parser("delete", help="delete a wire by uuid")
    p.add_argument("schematic", type=Path)
    p.add_argument("uuid", help="wire uuid")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_edit_wire_delete)

    # label
    label = esub.add_parser("label", help="edit labels")
    label_act = label.add_subparsers(dest="edit_action", required=True)
    p = label_act.add_parser("add", help="add a label")
    p.add_argument("schematic", type=Path)
    p.add_argument("kind", choices=["global", "hier", "local"])
    p.add_argument("name", help="label text")
    p.add_argument("xy", type=parse_xy, help="X,Y")
    p.add_argument("--rotation", type=float, default=0.0)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_edit_label_add)

    p = label_act.add_parser("move", help="move a label")
    p.add_argument("schematic", type=Path)
    p.add_argument("uuid", help="label uuid")
    p.add_argument("xy", type=parse_xy, help="X,Y")
    p.add_argument("--rotation", type=float, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_edit_label_move)

    p = label_act.add_parser("delete", help="delete a label")
    p.add_argument("schematic", type=Path)
    p.add_argument("uuid", help="label uuid")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_edit_label_delete)

    # junction
    junc = esub.add_parser("junction", help="edit junctions")
    junc_act = junc.add_subparsers(dest="edit_action", required=True)
    p = junc_act.add_parser("add", help="add a junction")
    p.add_argument("schematic", type=Path)
    p.add_argument("xy", type=parse_xy, help="X,Y")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_edit_junction_add)

    p = junc_act.add_parser("delete", help="delete a junction by uuid")
    p.add_argument("schematic", type=Path)
    p.add_argument("uuid", help="junction uuid")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_sch_edit_junction_delete)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kicad-tool",
        description="Repository-local KiCad inspection and validation helper.",
    )
    subcommands = parser.add_subparsers(dest="domain", required=True)

    sch = subcommands.add_parser("sch", help="schematic commands")
    sch_commands = sch.add_subparsers(dest="sch_command", required=True)

    render_region_parser = sch_commands.add_parser(
        "render-region",
        help="render a cropped PNG of a bbox region of a sheet",
    )
    render_region_parser.add_argument("schematic", type=Path)
    render_region_parser.add_argument("bbox", type=parse_bbox, help="X1,Y1,X2,Y2 in mm")
    render_region_parser.add_argument("-o", "--output", type=Path, default=Path("tmp/region.png"))
    render_region_parser.set_defaults(func=cmd_sch_render_region)

    inspect_parser = sch_commands.add_parser("inspect", help="inspect schematic score and visual collisions")
    inspect_parser.add_argument("schematic", type=Path)
    inspect_parser.add_argument("--only-text")
    inspect_parser.add_argument("--margin", type=float, default=0.0)
    inspect_parser.add_argument("--format", choices=["json", "text"], default="text")
    inspect_parser.set_defaults(func=cmd_sch_inspect)

    erc_parser = sch_commands.add_parser("erc", help="run schematic ERC")
    erc_parser.add_argument("schematic", type=Path)
    erc_parser.add_argument("-o", "--output", type=Path, default=Path("tmp/cupwarmer-erc.rpt"))
    erc_parser.add_argument("--format", choices=["json", "text"], default="text")
    erc_parser.set_defaults(func=cmd_sch_erc)

    netlist_parser = sch_commands.add_parser("netlist", help="export schematic netlist")
    netlist_parser.add_argument("schematic", type=Path)
    netlist_parser.add_argument("-o", "--output", type=Path, default=Path("tmp/cupwarmer-post.net"))
    netlist_parser.add_argument("--format", choices=["json", "text"], default="text")
    netlist_parser.set_defaults(func=cmd_sch_netlist)

    validate_parser = sch_commands.add_parser("validate", help="run schematic validation checks")
    validate_parser.add_argument("schematic", type=Path)
    validate_parser.add_argument("--sheet", type=Path, default=None)
    validate_parser.add_argument("--erc-report", type=Path, default=Path("tmp/cupwarmer-erc.rpt"))
    validate_parser.add_argument("--netlist-out", type=Path, default=Path("tmp/cupwarmer-post.net"))
    validate_parser.add_argument("--only-text")
    validate_parser.add_argument("--margin", type=float, default=0.0)
    validate_parser.add_argument("--save-baseline", type=Path, default=None,
                                 help="write erc/netlist/inspect snapshot to DIR")
    validate_parser.add_argument("--baseline", type=Path, default=None,
                                 help="diff current state against a saved baseline DIR")
    validate_parser.add_argument("--format", choices=["json", "text"], default="text")
    validate_parser.set_defaults(func=cmd_sch_validate)

    _add_query_subparsers(sch_commands)
    _add_edit_subparsers(sch_commands)

    pcb = subcommands.add_parser("pcb", help="PCB commands")
    pcb_commands = pcb.add_subparsers(dest="pcb_command", required=True)

    drc_parser = pcb_commands.add_parser("drc", help="run PCB DRC")
    drc_parser.add_argument("board", type=Path)
    drc_parser.add_argument("-o", "--output", type=Path, default=Path("tmp/cupwarmer-drc.rpt"))
    drc_parser.add_argument("--schematic-parity", action="store_true")
    drc_parser.add_argument("--format", choices=["json", "text"], default="text")
    drc_parser.set_defaults(func=cmd_pcb_drc)

    pcb_render_parser = pcb_commands.add_parser(
        "render-region",
        help="render a cropped PNG of a bbox region of a board",
    )
    pcb_render_parser.add_argument("board", type=Path)
    pcb_render_parser.add_argument("bbox", type=parse_bbox, help="X1,Y1,X2,Y2 in mm")
    pcb_render_parser.add_argument(
        "--layers", default=None,
        help=f"comma-separated layer names (default: {DEFAULT_PCB_RENDER_LAYERS})",
    )
    pcb_render_parser.add_argument("-o", "--output", type=Path, default=Path("tmp/pcb-region.png"))
    pcb_render_parser.set_defaults(func=cmd_pcb_render_region)

    pcb_query_parser = pcb_commands.add_parser("query", help="read-only PCB queries")
    pcb_qsub = pcb_query_parser.add_subparsers(dest="pcb_query_element", required=True)

    p = pcb_qsub.add_parser("list", help="list elements")
    p.add_argument("board", type=Path)
    p.add_argument(
        "element",
        choices=["footprints", "tracks", "vias", "zones", "drawings", "nets", "layers"],
    )
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_pcb_query_list)

    p = pcb_qsub.add_parser("footprint", help="query a footprint by reference")
    p.add_argument("board", type=Path)
    p.add_argument("ref", help="footprint reference, e.g. U1")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_pcb_query_footprint)

    p = pcb_qsub.add_parser("pad", help="query a pad (REF.PAD)")
    p.add_argument("board", type=Path)
    p.add_argument("ref_pad", help="REF.PAD, e.g. U1.A1")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_pcb_query_pad)

    p = pcb_qsub.add_parser("net", help="query a net by name or REF.PAD")
    p.add_argument("board", type=Path)
    p.add_argument(
        "target",
        help="net name or REF.PAD (REF.PAD only when both ref and pad exist)",
    )
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_pcb_query_net)

    p = pcb_qsub.add_parser("region", help="query elements intersecting a bbox")
    p.add_argument("board", type=Path)
    p.add_argument("bbox", type=parse_bbox, help="X1,Y1,X2,Y2 in mm")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_pcb_query_region)

    p = pcb_qsub.add_parser(
        "zone",
        help="query a single zone by uuid, name, or (net + layer)",
    )
    p.add_argument("board", type=Path)
    p.add_argument("--uuid", default=None, help="zone uuid")
    p.add_argument("--name", default=None, help="zone name")
    p.add_argument("--net", default=None, help="zone net name (requires --layer)")
    p.add_argument("--layer", default=None, help="zone layer (only with --net)")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_pcb_query_zone)

    # PCB edit subtree
    pcb_edit_parser = pcb_commands.add_parser("edit", help="PCB structural edits")
    pcb_esub = pcb_edit_parser.add_subparsers(dest="pcb_edit_element", required=True)

    fp = pcb_esub.add_parser("footprint", help="edit a footprint")
    fp_act = fp.add_subparsers(dest="pcb_edit_action", required=True)

    p = fp_act.add_parser("move", help="move a footprint by reference")
    p.add_argument("board", type=Path)
    p.add_argument("ref", help="footprint reference, e.g. R1")
    p.add_argument("xy", type=parse_xy, help="destination X,Y")
    p.add_argument("--rotation", type=float, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_pcb_edit_footprint_move)

    p = fp_act.add_parser(
        "move-property",
        help="move a single footprint property placement",
    )
    p.add_argument("board", type=Path)
    p.add_argument("ref", help="footprint reference, e.g. R1")
    p.add_argument("key", help="property key (e.g. Reference, Value)")
    p.add_argument("xy", type=parse_xy, help="destination X,Y")
    p.add_argument("--rotation", type=float, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_pcb_edit_footprint_move_property)

    p = fp_act.add_parser(
        "set-property",
        help="set a footprint property's string value",
    )
    p.add_argument("board", type=Path)
    p.add_argument("ref", help="footprint reference, e.g. R1")
    p.add_argument("key", help="property key (e.g. Value, Description, MPN, LCSC)")
    p.add_argument("value", help="new property value")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_pcb_edit_footprint_set_property)

    p = fp_act.add_parser(
        "move-layer",
        help="flip a footprint to F.Cu (front) or B.Cu (back)",
    )
    p.add_argument("board", type=Path)
    p.add_argument("ref", help="footprint reference, e.g. R1")
    p.add_argument("side", choices=["front", "back"])
    p.add_argument("--at", type=parse_xy, default=None,
                   help="optional new origin X,Y; defaults to current position")
    p.add_argument("--rotation", type=float, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_pcb_edit_footprint_move_layer)

    p = fp_act.add_parser("delete", help="delete a footprint by reference")
    p.add_argument("board", type=Path)
    p.add_argument("ref", help="footprint reference, e.g. R1")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_pcb_edit_footprint_delete)

    # zone edits
    zn = pcb_esub.add_parser("zone", help="edit a zone (copper pour)")
    zn_act = zn.add_subparsers(dest="pcb_edit_action", required=True)

    p = zn_act.add_parser(
        "set-polygon",
        help="replace a zone's polygon outline points",
    )
    p.add_argument("board", type=Path)
    p.add_argument("--uuid", default=None, help="zone uuid")
    p.add_argument("--name", default=None, help="zone name")
    p.add_argument("--net", default=None, help="zone net name (with --layer)")
    p.add_argument("--layer", default=None, help="zone layer (with --net)")
    p.add_argument(
        "points",
        nargs="+",
        help="polygon vertices as X,Y (at least 3)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_pcb_edit_zone_set_polygon)

    p = zn_act.add_parser(
        "add",
        help="add a new zone (copper pour)",
    )
    p.add_argument("board", type=Path)
    p.add_argument("net", help="net name (must exist in netlist)")
    p.add_argument("layer", help="layer name (e.g. F.Cu, B.Cu)")
    p.add_argument(
        "points",
        nargs="+",
        help="polygon vertices as X,Y (at least 3)",
    )
    p.add_argument("--copy-settings-from-uuid", default=None,
                   help="copy settings from an existing zone (primary path)")
    p.add_argument("--name", default=None, help="zone name (optional)")
    p.add_argument("--priority", type=int, default=None)
    p.add_argument("--clearance", type=float, default=None)
    p.add_argument("--min-thickness", type=float, default=None)
    p.add_argument("--thermal-gap", type=float, default=None)
    p.add_argument("--thermal-bridge-width", type=float, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_pcb_edit_zone_add)

    p = zn_act.add_parser(
        "delete",
        help="delete a zone by uuid, name, or (net + layer)",
    )
    p.add_argument("board", type=Path)
    p.add_argument("--uuid", default=None, help="zone uuid")
    p.add_argument("--name", default=None, help="zone name")
    p.add_argument("--net", default=None, help="zone net name (with --layer)")
    p.add_argument("--layer", default=None, help="zone layer (with --net)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_pcb_edit_zone_delete)

    p = zn_act.add_parser(
        "set-property",
        help="set a single zone property",
    )
    p.add_argument("board", type=Path)
    p.add_argument("--uuid", default=None, help="zone uuid")
    p.add_argument("--name", default=None, help="zone name")
    p.add_argument(
        "key",
        choices=[
            "priority",
            "clearance",
            "min_thickness",
            "thermal_gap",
            "thermal_bridge_width",
            "name",
        ],
    )
    p.add_argument("value", help="new value (int for priority, mm for others, string for name)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_pcb_edit_zone_set_property)

    # pcb sync
    sync_parser = pcb_commands.add_parser(
        "sync",
        help="add missing footprints AND update each pad's (net ...) to match the schematic netlist; idempotent",
    )
    sync_parser.add_argument("board", type=Path)
    sync_parser.add_argument("schematic", type=Path)
    sync_parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="write synced board to this path; leaves the input board untouched",
    )
    sync_parser.add_argument("--dry-run", action="store_true")
    sync_parser.add_argument("--format", choices=["json", "text"], default="text")
    sync_parser.set_defaults(func=cmd_pcb_sync)

    # pcb validate
    pcb_validate_parser = pcb_commands.add_parser(
        "validate",
        help="run PCB DRC + schematic/board parity",
    )
    pcb_validate_parser.add_argument("board", type=Path)
    pcb_validate_parser.add_argument("schematic", type=Path,
                                     help="top-level schematic")
    pcb_validate_parser.add_argument("--drc-report", type=Path,
                                     default=Path("tmp/pcb-validate-drc.rpt"))
    pcb_validate_parser.add_argument("--netlist-out", type=Path,
                                     default=Path("tmp/pcb-validate-sch-netlist.net"))
    pcb_validate_parser.add_argument("--save-baseline", type=Path, default=None,
                                     help="write current drc/netlist/parity to DIR")
    pcb_validate_parser.add_argument("--baseline", type=Path, default=None,
                                     help="diff current state against a saved baseline DIR")
    pcb_validate_parser.add_argument("--format", choices=["json", "text"], default="text")
    pcb_validate_parser.set_defaults(func=cmd_pcb_validate)

    _attach_subparser_refs(parser)
    return parser


def _attach_subparser_refs(parser: argparse.ArgumentParser) -> None:
    """Attach a `subparser` default to every leaf parser so `_print_help_for`
    can show that parser's usage on runtime failure."""
    has_sub = False
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            has_sub = True
            for sp in action.choices.values():
                _attach_subparser_refs(sp)
    if not has_sub:
        parser.set_defaults(subparser=parser)


def _print_help_for(args: argparse.Namespace) -> None:
    p = getattr(args, "subparser", None)
    example = getattr(args, "example", None)
    if p is not None:
        sys.stderr.write("\n")
        sys.stderr.write(p.format_usage())
    if example:
        sys.stderr.write(f"example: {example}\n")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr or "")
        return exc.returncode
    except (FileNotFoundError, ValueError, KeyError, LookupError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        _print_help_for(args)
        return 2
    except Exception as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        _print_help_for(args)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
