---
name: kicad-sch-cleanup-loop
description: Iteratively improve a KiCad schematic sheet's `sch inspect` score, then tighten label/value placement. Use after functional edits when the user asks to clean up, tidy, or improve the visual score of a sheet.
---

# KiCad Schematic Visual Cleanup Loop

Use this skill for score-driven schematic visual cleanup. Use `kicad-tool` for commands and `kicad-workflow` for edit/validation rules.

Inputs:

- `<top>`: top-level schematic for ERC/netlist/validate.
- `<sheet>`: child schematic sheet being visually cleaned.

## CLI-Only

Do not read full `.kicad_sch` files into context. Use `sch query` for scoped JSON, `sch render-region` for bounded PNGs, and `sch edit` for mutations. If a needed query or edit command is missing, extend `kicad-tool`.

## Baseline

```bash
python3 <kicad_tool.py> sch validate <top> --sheet <sheet> --save-baseline tmp/cleanup-baseline > tmp/cleanup-baseline.json
```

Record `score.total`, collisions, and symbol-wire conflicts.

## Phase 0: Area Plan

Create `tmp/cleanup-plan.md` before editing.

1. Choose the target bbox from the user request and latest inspect/validate JSON, expanded enough to include local context.
2. Render `tmp/cleanup-overview.png` via `sch render-region` and inspect it alongside JSON.
3. Cluster issues by spatial locality and topological coupling.
4. Order clusters with anchors first, topology before cosmetics, and independent clusters where they unblock more downstream work.
5. Defer label/value placement until component and wire topology stabilizes.
6. For any wire relocation or deletion, enumerate all taps using `sch query region` over the wire bbox plus margin.

## Phase 1: Execute Clusters

For each cluster:

1. Re-render the cluster to `tmp/cleanup-area.png`.
2. Query current objects in the bbox.
3. Apply one scoped edit sequence.
4. Validate against `tmp/cleanup-baseline`.

Hard gate: connectivity regression means immediate revert. New ERC errors, removed nets, or removed nodes are not tolerated.

Soft gate: score regression alone is exploratory. Keep at most three exploratory cluster steps without recovering to the last-good score, and roll back if the overview diverges from the plan or score jumps by roughly more than 20%.

Refresh `tmp/cleanup-baseline` only after accepting a new last-good state.

## Phase 2: Label And Value Tightening

After topology stabilizes, tighten text placement. This phase is cosmetic only:

- Accept only if ERC parity holds, score does not worsen, and no new collisions appear.
- Use render first, then probe actual text bbox via `sch inspect`.
- Derive final anchor placement from probed bbox offsets; do not estimate text width from character count.
- Prefer local property moves over symbol moves.

## Report

```text
Sheet: <sheet>
Baseline: total=<n0>
Phase 1: total=<n1> accepted=<a1> reverts=<r1>
Phase 2: accepted=<a2>
Final: ERC=<status> netlist=<status>
```

Show `git diff --stat <sheet>`. Do not commit unless asked.

## Hard Rules

- No cleanup edit before `tmp/cleanup-plan.md` exists.
- One cluster, then one validation.
- Connectivity regression means immediate revert.
- Final state must be a last-good state.
- Reuse stable temporary filenames: `cleanup-baseline.json`, `cleanup-latest.json`, `cleanup-overview.png`, `cleanup-area.png`, and `cleanup-plan.md`.
