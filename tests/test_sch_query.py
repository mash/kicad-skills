"""Success-path tests for each ``sch query`` leaf subcommand."""
from __future__ import annotations

from pathlib import Path

from tests.conftest import needs_kicad_cli
from tests.helpers import run_cli


def test_sch_query_symbol(sch_fixture: Path) -> None:
    out = run_cli("sch", "query", "symbol", str(sch_fixture), "R1")
    assert out["found"] is True
    assert out["ref"] == "R1"
    assert out["lib_id"] == "minimal:R"


def test_sch_query_symbol_text_format(sch_fixture: Path) -> None:
    """Text-format dispatch coverage (only one such case in the whole suite)."""
    stdout = run_cli(
        "sch", "query", "symbol", str(sch_fixture), "R1", format="text"
    )
    assert isinstance(stdout, str)
    assert stdout.strip() != ""
    assert "R1" in stdout


def test_sch_query_pin_no_netlist(sch_fixture: Path) -> None:
    out = run_cli(
        "sch", "query", "pin", str(sch_fixture), "R1.1", "--no-netlist"
    )
    assert out["found"] is True
    assert out["ref"] == "R1"
    assert out["pin"] == "1"
    # --no-netlist: skip auto-resolution, net should remain None
    assert out["net"] is None


@needs_kicad_cli
def test_sch_query_net(sch_fixture: Path) -> None:
    # query net has no --no-netlist; needs kicad-cli to generate the netlist.
    out = run_cli("sch", "query", "net", str(sch_fixture), "R1.1")
    assert "found" in out
    if out["found"]:
        assert "nodes" in out


def test_sch_query_region(sch_fixture: Path) -> None:
    out = run_cli(
        "sch", "query", "region", str(sch_fixture), "0,0,200,200"
    )
    assert "symbols" in out
    assert "wires" in out
    assert "labels" in out
    refs = {item["ref"] for item in out["symbols"]}
    assert "R1" in refs


def test_sch_query_wire_at(sch_fixture: Path) -> None:
    # Wire endpoint at 50.8,50.8 exists in the fixture.
    out = run_cli(
        "sch", "query", "wire", str(sch_fixture), "--at", "50.8,50.8"
    )
    assert out["found"] is True
    assert len(out["wires"]) >= 1


def test_sch_query_label_by_name(sch_fixture: Path) -> None:
    out = run_cli(
        "sch", "query", "label", str(sch_fixture), "--name", "NET1"
    )
    assert out["found"] is True
    texts = {lab["text"] for lab in out["labels"]}
    assert "NET1" in texts


def test_sch_query_lib_symbol(sch_fixture: Path) -> None:
    out = run_cli("sch", "query", "lib-symbol", str(sch_fixture), "minimal:R")
    assert out["found"] is True
    # Resistor has 2 pins.
    assert len(out["pins"]) >= 1


def test_sch_query_list_wires(sch_fixture: Path) -> None:
    out = run_cli("sch", "query", "list", str(sch_fixture), "wires")
    assert out["element"] == "wires"
    assert len(out["items"]) >= 1
    # Each wire has p1/p2 endpoints.
    assert "p1" in out["items"][0]
    assert "p2" in out["items"][0]
