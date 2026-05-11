"""Shared helpers for the kicad-tool CLI test suite."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def run_cli(
    *args: str,
    format: str = "json",
    check: bool = True,
    cwd: Path | str | None = None,
) -> Any:
    """Invoke ``python -m kicad_tool`` and return parsed output.

    - ``format="json"`` parses stdout as JSON and returns the resulting
      ``dict``/``list``.
    - Any other value (e.g. ``"text"``) returns stdout verbatim as ``str``.
    - On non-zero exit (when ``check=True``) raises ``RuntimeError`` with
      stderr included for easier debugging.
    """
    argv = [sys.executable, "-m", "kicad_tool", *args, "--format", format]
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"kicad-tool exited {proc.returncode}\n"
            f"argv: {argv}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    if format == "json":
        return json.loads(proc.stdout)
    return proc.stdout
