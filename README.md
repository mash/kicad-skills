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

Ask your coding agent to import either the whole repository or a single skill folder into its local skill/search path.

Whole repository:

```text
Import https://github.com/mash/kicad-skills as local KiCad skills for this project.
Use all skills under skills/ when working with KiCad schematic or PCB files.
```

Single skill:

```text
Import https://github.com/mash/kicad-skills/tree/main/skills/kicad-tool as a local skill.
Use it whenever you need to query, edit, render, or validate KiCad files.
```

For agents that do not manage skills automatically, copy the desired folders under `skills/` into the agent's local skill directory. Keep each skill folder intact, including its `SKILL.md`, `agents/`, and `scripts/` files.

## Dependencies

The bundled scripts require the `kiutils` fork published at `https://github.com/mash/kiutils`.

Install the Python dependencies from this repository before running the scripts:

```bash
pip install .
```

Example prompts after import:

```text
Use the KiCad skills to inspect this schematic sheet and summarize the current ERC/netlist status.
```

```text
Use the KiCad workflow skill to make this schematic edit safely, including dry-run, validation, and a concise report.
```

```text
Use the KiCad schematic cleanup loop to improve the visual score of this sheet without changing connectivity.
```

## Scope

This repository intentionally contains KiCad file/tool/workflow skills only. Project-specific electrical design rules, board constraints, and product decisions should live in the consuming repository.

## Status

Early extraction from an active KiCad hardware workflow. APIs and skill wording may change while the plugin is being hardened.
