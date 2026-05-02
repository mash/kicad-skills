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
REPO_ROOT = SCRIPT_DIR.parents[3]
DEFAULT_KICAD_CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import score_schematic as schematic_score
import sch_query
import sch_edit
import sch_netlist


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


PX_PER_MM = 12.0


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
    if args.format == "text":
        print(png)
    else:
        print_json({"png": str(png), "bbox": list(args.bbox)})
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
    args.report.parent.mkdir(parents=True, exist_ok=True)
    result = command_result(
        "sch erc",
        [
            kicad_cli(),
            "sch",
            "erc",
            "--exit-code-violations",
            "--output",
            str(args.report),
            str(args.schematic),
        ],
    )
    if args.format == "text":
        print(f"ERC: exit={result['returncode']} report={args.report}")
        if result["stderr"]:
            sys.stderr.write(result["stderr"])
    else:
        print_json({"erc": result, "report": str(args.report)})
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


def cmd_pcb_drc(args: argparse.Namespace) -> int:
    args.report.parent.mkdir(parents=True, exist_ok=True)
    command = [kicad_cli(), "pcb", "drc"]
    if args.schematic_parity:
        command.append("--schematic-parity")
    command.extend(["--output", str(args.report), str(args.board)])
    result = command_result("pcb drc", command)
    if args.format == "text":
        print(f"DRC: exit={result['returncode']} report={args.report}")
        if result["stderr"]:
            sys.stderr.write(result["stderr"])
    else:
        print_json({"drc": result, "report": str(args.report)})
    return result["returncode"]


# ---------------------------------------------------------------------------
# sch query handlers
# ---------------------------------------------------------------------------


def _emit_query(args: argparse.Namespace, payload: dict[str, Any]) -> int:
    print_json(payload)
    return 0 if payload.get("found", True) else 1


def cmd_sch_query_symbol(args: argparse.Namespace) -> int:
    return _emit_query(args, sch_query.query_symbol(args.schematic, args.ref))


def cmd_sch_query_pin(args: argparse.Namespace) -> int:
    return _emit_query(
        args,
        sch_query.query_pin(args.schematic, args.ref_pin, netlist_path=args.netlist),
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
        sch_query.query_list(args.schematic, args.element, netlist_path=args.netlist),
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
        pt_from=args.from_pt,
        pt_to=args.to_pt,
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
        justify=args.justify,
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
        at=args.at,
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
    p.add_argument("--format", choices=["json", "text"], default="json")
    p.set_defaults(func=cmd_sch_query_symbol)

    p = qsub.add_parser("pin", help="query a pin (REF.PIN)")
    p.add_argument("schematic", type=Path)
    p.add_argument("ref_pin", help="REF.PIN, e.g. U1.F3")
    p.add_argument("--netlist", type=Path, default=None)
    p.add_argument("--format", choices=["json", "text"], default="json")
    p.set_defaults(func=cmd_sch_query_pin)

    p = qsub.add_parser("net", help="query a net by name or REF.PIN")
    p.add_argument("schematic", type=Path)
    p.add_argument("target", help="net name or REF.PIN (dispatched by '.')")
    p.add_argument("--netlist", type=Path, required=True)
    p.add_argument("--format", choices=["json", "text"], default="json")
    p.set_defaults(func=cmd_sch_query_net)

    p = qsub.add_parser("region", help="query elements in a bbox")
    p.add_argument("schematic", type=Path)
    p.add_argument("bbox", type=parse_bbox, help="X1,Y1,X2,Y2")
    p.add_argument("--format", choices=["json", "text"], default="json")
    p.set_defaults(func=cmd_sch_query_region)

    p = qsub.add_parser("wire", help="query wire segments")
    p.add_argument("schematic", type=Path)
    p.add_argument("--uuid")
    p.add_argument("--at", type=parse_xy, help="endpoint X,Y")
    p.add_argument("--through", type=parse_xy, help="pass-through X,Y")
    p.add_argument("--format", choices=["json", "text"], default="json")
    p.set_defaults(func=cmd_sch_query_wire)

    p = qsub.add_parser("label", help="query labels")
    p.add_argument("schematic", type=Path)
    p.add_argument("--name")
    p.add_argument("--uuid")
    p.add_argument("--format", choices=["json", "text"], default="json")
    p.set_defaults(func=cmd_sch_query_label)

    p = qsub.add_parser("lib-symbol", help="query a library symbol's pins")
    p.add_argument("schematic", type=Path)
    p.add_argument("lib_id", help='e.g. "project:PLACEHOLDER_1"')
    p.add_argument("--format", choices=["json", "text"], default="json")
    p.set_defaults(func=cmd_sch_query_lib_symbol)

    p = qsub.add_parser("list", help="list elements")
    p.add_argument("schematic", type=Path)
    p.add_argument("element", choices=["symbols", "labels", "wires", "junctions", "nets"])
    p.add_argument("--netlist", type=Path, default=None)
    p.add_argument("--format", choices=["json", "text"], default="json")
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
    p.add_argument("--format", choices=["json", "text"], default="json")
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
    p.add_argument("--format", choices=["json", "text"], default="json")
    p.set_defaults(func=cmd_sch_edit_symbol_move_property)

    p = sym_act.add_parser(
        "add-pin",
        help="add a pin to a symbol (updates embedded lib_symbols + standalone .kicad_sym)",
    )
    p.add_argument("schematic", type=Path)
    p.add_argument("lib_id", help='e.g. "project:PLACEHOLDER_1"')
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
        "--lib-file", dest="lib_file", type=Path, default=None,
        help="optional standalone .kicad_sym file to update with the embedded symbol",
    )
    p.add_argument("--font-size", dest="font_size", type=float, default=1.27)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="json")
    p.set_defaults(func=cmd_sch_edit_symbol_add_pin)

    # wire
    wire = esub.add_parser("wire", help="edit wires")
    wire_act = wire.add_subparsers(dest="edit_action", required=True)
    p = wire_act.add_parser("add", help="add a wire")
    p.add_argument("schematic", type=Path)
    p.add_argument("from_pt", type=parse_xy, metavar="FROM", help="X1,Y1")
    p.add_argument("to_pt", type=parse_xy, metavar="TO", help="X2,Y2")
    p.add_argument("--type", choices=["solid", "default"], default="default")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="json")
    p.set_defaults(func=cmd_sch_edit_wire_add)

    p = wire_act.add_parser("delete", help="delete a wire")
    p.add_argument("schematic", type=Path)
    p.add_argument("--uuid")
    p.add_argument("--from", dest="from_pt", type=parse_xy, default=None, help="X,Y")
    p.add_argument("--to", dest="to_pt", type=parse_xy, default=None, help="X,Y")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="json")
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
    p.add_argument("--justify", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="json")
    p.set_defaults(func=cmd_sch_edit_label_add)

    p = label_act.add_parser("move", help="move a label")
    p.add_argument("schematic", type=Path)
    p.add_argument("uuid", help="label uuid")
    p.add_argument("xy", type=parse_xy, help="X,Y")
    p.add_argument("--rotation", type=float, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="json")
    p.set_defaults(func=cmd_sch_edit_label_move)

    p = label_act.add_parser("delete", help="delete a label")
    p.add_argument("schematic", type=Path)
    p.add_argument("uuid", help="label uuid")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="json")
    p.set_defaults(func=cmd_sch_edit_label_delete)

    # junction
    junc = esub.add_parser("junction", help="edit junctions")
    junc_act = junc.add_subparsers(dest="edit_action", required=True)
    p = junc_act.add_parser("add", help="add a junction")
    p.add_argument("schematic", type=Path)
    p.add_argument("xy", type=parse_xy, help="X,Y")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="json")
    p.set_defaults(func=cmd_sch_edit_junction_add)

    p = junc_act.add_parser("delete", help="delete a junction")
    p.add_argument("schematic", type=Path)
    p.add_argument("--uuid")
    p.add_argument("--at", type=parse_xy, default=None, help="X,Y")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="json")
    p.set_defaults(func=cmd_sch_edit_junction_delete)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kicad-tool",
        description="KiCad inspection and validation helper.",
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
    render_region_parser.add_argument("--output", type=Path, default=Path("tmp/region.png"))
    render_region_parser.add_argument("--format", choices=["json", "text"], default="json")
    render_region_parser.set_defaults(func=cmd_sch_render_region)

    inspect_parser = sch_commands.add_parser("inspect", help="inspect schematic score and visual collisions")
    inspect_parser.add_argument("schematic", type=Path)
    inspect_parser.add_argument("--only-text")
    inspect_parser.add_argument("--margin", type=float, default=0.0)
    inspect_parser.add_argument("--format", choices=["json", "text"], default="json")
    inspect_parser.set_defaults(func=cmd_sch_inspect)

    erc_parser = sch_commands.add_parser("erc", help="run schematic ERC")
    erc_parser.add_argument("schematic", type=Path)
    erc_parser.add_argument("--report", type=Path, default=Path("tmp/kicad-erc.rpt"))
    erc_parser.add_argument("--format", choices=["json", "text"], default="json")
    erc_parser.set_defaults(func=cmd_sch_erc)

    netlist_parser = sch_commands.add_parser("netlist", help="export schematic netlist")
    netlist_parser.add_argument("schematic", type=Path)
    netlist_parser.add_argument("--output", type=Path, default=Path("tmp/kicad-post.net"))
    netlist_parser.add_argument("--format", choices=["json", "text"], default="json")
    netlist_parser.set_defaults(func=cmd_sch_netlist)

    validate_parser = sch_commands.add_parser("validate", help="run schematic validation checks")
    validate_parser.add_argument("schematic", type=Path)
    validate_parser.add_argument("--sheet", type=Path, default=None)
    validate_parser.add_argument("--erc-report", type=Path, default=Path("tmp/kicad-erc.rpt"))
    validate_parser.add_argument("--netlist-out", type=Path, default=Path("tmp/kicad-post.net"))
    validate_parser.add_argument("--only-text")
    validate_parser.add_argument("--margin", type=float, default=0.0)
    validate_parser.add_argument("--save-baseline", type=Path, default=None,
                                 help="write erc/netlist/inspect snapshot to DIR")
    validate_parser.add_argument("--baseline", type=Path, default=None,
                                 help="diff current state against a saved baseline DIR")
    validate_parser.add_argument("--format", choices=["json", "text"], default="json")
    validate_parser.set_defaults(func=cmd_sch_validate)

    _add_query_subparsers(sch_commands)
    _add_edit_subparsers(sch_commands)

    pcb = subcommands.add_parser("pcb", help="PCB commands")
    pcb_commands = pcb.add_subparsers(dest="pcb_command", required=True)

    drc_parser = pcb_commands.add_parser("drc", help="run PCB DRC")
    drc_parser.add_argument("board", type=Path)
    drc_parser.add_argument("--report", type=Path, default=Path("tmp/kicad-drc.rpt"))
    drc_parser.add_argument("--schematic-parity", action="store_true")
    drc_parser.add_argument("--format", choices=["json", "text"], default="json")
    drc_parser.set_defaults(func=cmd_pcb_drc)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr or "")
        return exc.returncode
    except Exception as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
