"""Pytest fixtures + markers for the kicad-tool CLI test suite."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# --- kicad-cli detection ---------------------------------------------------
KICAD_CLI = shutil.which("kicad-cli")
needs_kicad_cli = pytest.mark.skipif(
    KICAD_CLI is None,
    reason="kicad-cli not on PATH",
)


# --- module-importability sanity check -------------------------------------
def _ensure_module_importable() -> None:
    """Verify ``python -m kicad_tool`` works; raise an informative error if not."""
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_tool", "--help"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "`python -m kicad_tool` failed. Did you run `pip install -e \".[dev]\"`?\n"
            f"stderr:\n{proc.stderr}"
        )


_ensure_module_importable()


# --- fixtures --------------------------------------------------------------
def _copy_all(tmp_path: Path) -> Path:
    """Copy schematic, PCB, and project file into a single tmp dir."""
    for name in ("minimal.kicad_sch", "minimal.kicad_pcb", "minimal.kicad_pro"):
        shutil.copy(FIXTURES_DIR / name, tmp_path / name)
    return tmp_path


@pytest.fixture
def sch_fixture(tmp_path: Path) -> Path:
    """Copy fixtures into tmp_path and return the schematic path."""
    _copy_all(tmp_path)
    return tmp_path / "minimal.kicad_sch"


@pytest.fixture
def pcb_fixture(tmp_path: Path) -> Path:
    """Copy fixtures into tmp_path and return the PCB path."""
    _copy_all(tmp_path)
    return tmp_path / "minimal.kicad_pcb"
