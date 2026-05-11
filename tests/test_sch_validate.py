"""Success-path tests for ``sch`` subcommands that depend on ``kicad-cli``.

All tests are gated by :data:`needs_kicad_cli`; they skip when ``kicad-cli`` is
not on PATH.
"""
from __future__ import annotations

from pathlib import Path

from tests.conftest import needs_kicad_cli
from tests.helpers import run_cli


@needs_kicad_cli
def test_sch_erc(sch_fixture: Path) -> None:
    out_path = sch_fixture.parent / "erc.rpt"
    out = run_cli(
        "sch", "erc", str(sch_fixture),
        "-o", str(out_path),
    )
    assert "erc" in out
    assert out["report"] == str(out_path)
    assert out_path.exists()


@needs_kicad_cli
def test_sch_netlist(sch_fixture: Path) -> None:
    out_path = sch_fixture.parent / "netlist.net"
    out = run_cli(
        "sch", "netlist", str(sch_fixture),
        "-o", str(out_path),
    )
    assert "netlist" in out
    assert out["output"] == str(out_path)
    assert out_path.exists()


@needs_kicad_cli
def test_sch_validate(sch_fixture: Path) -> None:
    erc_report = sch_fixture.parent / "erc.rpt"
    netlist_out = sch_fixture.parent / "netlist.net"
    out = run_cli(
        "sch", "validate", str(sch_fixture),
        "--sheet", str(sch_fixture),
        "--erc-report", str(erc_report),
        "--netlist-out", str(netlist_out),
    )
    # Default-mode payload shape (see cmd_sch_validate).
    assert "erc" in out
    assert "netlist" in out
    assert "inspect" in out
    assert out["erc"]["report"] == str(erc_report)
    assert out["netlist"]["output"] == str(netlist_out)


@needs_kicad_cli
def test_sch_inspect(sch_fixture: Path) -> None:
    out = run_cli("sch", "inspect", str(sch_fixture))
    assert "score" in out
    score = out["score"]
    assert "total" in score
    assert "collision_count" in score
    assert "wire_corner_count" in score


@needs_kicad_cli
def test_sch_render_region(sch_fixture: Path) -> None:
    png_path = sch_fixture.parent / "region.png"
    # render-region prints the resulting path to stdout (not JSON).
    out = run_cli(
        "sch", "render-region", str(sch_fixture), "0,0,200,200",
        "-o", str(png_path),
        format="text",
    )
    assert str(png_path) in out
    assert png_path.exists()
