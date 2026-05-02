# KiCad Skills

AI-agent skills and scripts for working with KiCad schematic and PCB design files.

## Contents

- `kicad-tool`: unified CLI for schematic query/edit/render/validate and PCB DRC.
- `kicad-workflow`: safe edit and validation workflow for KiCad hardware files.
- `kicad-sch-cleanup-loop`: iterative visual cleanup workflow for schematic sheets.

## Layout

```text
skills/
  kicad-tool/
  kicad-workflow/
  kicad-sch-cleanup-loop/
```

## Usage

Reference this directory from your agent runtime, or copy the relevant skill folders into the agent's skill/search path.

The CLI entrypoint is:

```bash
python3 skills/kicad-tool/scripts/kicad_tool.py <domain> <command> ...
```

Example:

```bash
python3 skills/kicad-tool/scripts/kicad_tool.py sch validate path/to/top.kicad_sch --sheet path/to/edited.kicad_sch
```

Set `KICAD_CLI` if KiCad is not installed at the default macOS app path:

```bash
KICAD_CLI=/path/to/kicad-cli python3 skills/kicad-tool/scripts/kicad_tool.py --help
```

## Scope

This repository intentionally contains KiCad file/tool/workflow skills only. Project-specific electrical design rules, board constraints, and product decisions should live in the consuming repository.

## Status

Early extraction from an active KiCad hardware workflow. APIs and skill wording may change while the plugin is being hardened.
