---
name: kicad-workflow
description: Workflow for safely editing and validating KiCad hardware design files. Use with `kicad-tool` for schematic edits, dry-run/apply loops, ERC/netlist/inspect validation, baseline diffs, and PCB DRC after board changes.
---

# KiCad Workflow

Use this skill for the human and agent workflow around KiCad edits. Use `kicad-tool` for exact commands.

Project-specific facts such as top schematic path, sheet inventory, board stackup, electrical partitioning, and placement rules belong in the consuming repository's docs or repo-local skills. Read those before choosing what to change.

## File Types

| File | Approach |
|---|---|
| `.kicad_sch` | Use `sch query` and `sch edit` via `kicad-tool`. Preserve formatting and UUIDs. |
| `.kicad_pcb` | Use PCB CLI support when available; otherwise make minimal local S-expression edits only. |
| `.kicad_pro` | JSON edit only when needed. |
| `.kicad_prl` | Do not edit unless explicitly asked. |
| `.kicad_sym` | Keep standalone symbols in sync with schematic embedded `lib_symbols` when changing custom symbols. |

## Required Editing Loop

1. Read the project hardware docs to identify `<top>`, `<sheet>`, `<board>`, and local design constraints.
2. Use `kicad-tool` queries to gather coordinates and current connectivity.
3. For structural schematic edits, run `sch edit ... --dry-run` first.
4. Apply the smallest change that satisfies the request.
5. Validate with `sch validate` or, for board changes, `pcb drc`.
6. Review `git diff` for unintended file churn, UUID churn, net changes, and symbol-library drift.

## Planning Gate

Before any non-trivial `sch edit`, state and wait for approval unless the user gave an exact single action or explicitly asked for autonomous iteration.

Include:

1. Goal: intended design behavior.
2. Problem: what is missing or wrong, citing symbols/nets/pins from `sch query`.
3. Approach: proposed change and alternatives considered.
4. Placement: where moved/new items will sit relative to existing anchors.
5. Layout sketch: current and proposed local topology in small ASCII diagrams.

Coordinates support the sketch; they do not replace it.

## Pre-Edit Baseline

For routine schematic edits:

```bash
python3 <kicad_tool.py> sch validate <top> --sheet <edited-sheet>
```

For non-trivial structural edits, save a baseline first:

```bash
python3 <kicad_tool.py> sch validate <top> --save-baseline tmp/kicad-baseline
```

After editing:

```bash
python3 <kicad_tool.py> sch validate <top> --baseline tmp/kicad-baseline
```

Baseline validation exits non-zero on regressions such as new ERC errors, removed nets, or removed nodes from existing nets.

## Wire Delete And Replace

KiCad wires can carry connections anywhere along a segment, not only at endpoints. Before deleting or replacing a wire:

1. Query the wire bbox with about 0.01 mm margin.
2. Enumerate every tap: wire endpoints, junctions, perpendicular endpoints, pin endpoints, and label anchors.
3. Ensure the replacement reconnects each tap.
4. Validate with a saved baseline; ERC parity alone can miss orphaned mid-span taps.

## Validation Rules

- Run ERC, netlist, and validate from the top-level schematic.
- Run inspect/render on the edited child sheet.
- Existing ERC violations are not automatically failures; new violations are.
- For visual-only edits, netlist diffs should be metadata-only.
- For electrical edits, summarize the intended netlist changes.
- A successful CLI exit is necessary but not sufficient; inspect `git diff` and expected connectivity.

## Board Changes

For `.kicad_pcb` changes:

- Keep `layers`, `setup`, `setup.stackup`, `Edge.Cuts`, and title block stable unless the task explicitly targets them.
- Treat `net`, `footprint`, `segment`, `via`, and `zone` as high-risk fields.
- Run PCB DRC after board edits.
- If schematic/board parity is relevant, use the parity mode exposed by `kicad-tool`.
- Document known KiCad CLI limitations separately from true DRC violations.

## Stage Boundary

This workflow owns functional correctness: connectivity, nets, symbols, hierarchy, ERC, netlist, and PCB DRC.

Do not chase schematic visual score, text collisions, wire-corner count, or label/value tightening here unless the user specifically asks. Use `kicad-sch-cleanup-loop` for score-driven schematic cleanup.

## Report Format

Keep the report short and specific:

```text
ERC: pass
Netlist: intended changes only
Inspect: total=<score> collisions=<count>
PCB DRC: pass
```

On failure, include the failing stage, report path, and the first actionable issue.
