"""Success-path tests for ``pcb`` subcommands that depend on ``kicad-cli``.

All tests are gated by :data:`needs_kicad_cli`; they skip when ``kicad-cli`` is
not on PATH.
"""
from __future__ import annotations

from pathlib import Path

from tests.conftest import needs_kicad_cli
from tests.helpers import run_cli


@needs_kicad_cli
def test_pcb_drc(pcb_fixture: Path) -> None:
    out_path = pcb_fixture.parent / "drc.rpt"
    out = run_cli(
        "pcb", "drc", str(pcb_fixture),
        "-o", str(out_path),
    )
    assert "drc" in out
    assert out["report"] == str(out_path)
    assert out_path.exists()


@needs_kicad_cli
def test_pcb_sync_dry_run(pcb_fixture: Path) -> None:
    sch = pcb_fixture.parent / "minimal.kicad_sch"
    out = run_cli(
        "pcb", "sync", str(pcb_fixture), str(sch),
        "--dry-run",
    )
    # See cmd_pcb_sync: payload has action/changed/wrote/target/details.
    assert "details" in out
    assert "changed" in out
    assert "parity" in out["details"]


@needs_kicad_cli
def test_pcb_validate(pcb_fixture: Path) -> None:
    sch = pcb_fixture.parent / "minimal.kicad_sch"
    drc_report = pcb_fixture.parent / "drc.rpt"
    netlist_out = pcb_fixture.parent / "sch-netlist.net"
    out = run_cli(
        "pcb", "validate", str(pcb_fixture), str(sch),
        "--drc-report", str(drc_report),
        "--netlist-out", str(netlist_out),
    )
    # Default-mode payload (see cmd_pcb_validate).
    assert "drc" in out
    assert "netlist" in out
    assert "parity" in out


@needs_kicad_cli
def test_pcb_render_region(pcb_fixture: Path) -> None:
    png_path = pcb_fixture.parent / "region.png"
    out = run_cli(
        "pcb", "render-region", str(pcb_fixture), "0,0,200,200",
        "-o", str(png_path),
        format="text",
    )
    assert str(png_path) in out
    assert png_path.exists()
