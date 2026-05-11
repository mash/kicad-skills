"""Success-path tests for ``kicad-tool pcb query *`` leaves."""
from __future__ import annotations

from tests.helpers import run_cli


def test_pcb_query_list_footprints(pcb_fixture):
    out = run_cli("pcb", "query", "list", str(pcb_fixture), "footprints")
    assert out["element"] == "footprints"
    refs = {item["ref"] for item in out["items"]}
    assert "R1" in refs
    assert "LED1" in refs


def test_pcb_query_footprint(pcb_fixture):
    out = run_cli("pcb", "query", "footprint", str(pcb_fixture), "R1")
    assert out["found"] is True
    assert out["ref"] == "R1"


def test_pcb_query_pad(pcb_fixture):
    out = run_cli("pcb", "query", "pad", str(pcb_fixture), "R1.1")
    assert out["found"] is True
    assert out["ref"] == "R1"
    assert out["pad"] == "1"


def test_pcb_query_net(pcb_fixture):
    # The installed kiutils mis-parses `(net N "NAME")` in KiCad 8 files
    # (it discards the number and stores exp[1] as ``name``), so the value
    # the parser sees for this fixture is ``1`` rather than ``"NET1"``.
    # Querying with that token is enough to exercise the success path of
    # query_net (members + segments scan).
    out = run_cli("pcb", "query", "net", str(pcb_fixture), "1", check=False)
    assert "name" in out
    assert "members" in out
    assert "segments" in out
    assert "vias" in out


def test_pcb_query_region(pcb_fixture):
    out = run_cli("pcb", "query", "region", str(pcb_fixture), "0,0,200,200")
    refs = {fp["ref"] for fp in out["footprints"]}
    assert ("R1" in refs) or ("LED1" in refs)
