---
name: kicad-workflow
description: Use when editing KiCad schematics or PCBs — adding/removing/modifying symbols, wires, labels, or footprints, or validating changes (ERC, netlist, DRC, baseline diff). Stage-1 functional workflow; pair with `kicad-tool` for commands. For visual/score polish use `kicad-sch-cleanup-loop` instead.
---

# KiCad Workflow

Editing **process** for KiCad files: planning, sequencing, validating. Owns the _how/when_; commands and CLI shape live in `kicad-tool` — read it first via the Skill tool. Project-specific facts (top-level path, sheet inventory, stackup, partitioning) live in the consuming repo's `hardware/` docs.

## File types

| File | Approach |
|---|---|
| `.kicad_sch` | `kicad-tool sch query` / `sch edit` |
| `.kicad_pcb` | `kicad-tool pcb edit` when available; otherwise minimal local S-expr edits |
| `.kicad_pro` | JSON edit only when needed; minimal |
| `.kicad_prl` | Do not edit unless explicitly asked |
| `.kicad_sym` | Keep in sync with the schematic's embedded `lib_symbols` when changing custom symbols (`sch edit symbol add-pin` does this automatically) |

If `sch edit` / `pcb edit` lacks a write subcommand, **extend kicad-tool first**. Raw `Edit`/`Write` on `.kicad_sch`/`.kicad_pcb` is the last resort and requires explicit user approval.

## Wire delete / replace — audit mid-span taps first

A wire can carry connections **anywhere along its length** — junctions, perpendicular wire endpoints, pin endpoints, label anchors — not only at its two endpoints. Endpoint-only stub replacement orphans mid-span taps; ERC misses it (the leftover junction keeps the count parity-clean).

Before any `sch edit wire delete` / replace:

1. Query the wire bbox with about 0.01 mm margin (`sch query region`).
2. Enumerate every tap.
3. Ensure the replacement reconnects each tap.
4. Validate via baseline-diff — its `removed_nodes` check is what actually catches this.

## Stage 1 workflow

This skill is **stage 1: functional editing**. Score / layout cleanup is stage 2 (`kicad-sch-cleanup-loop`).

### 0. Plan and get approval (mandatory)

Bird's-eye self-check, three zoom levels — one sentence each:

- **Board**: does this change fit the project's critical path now, or should it wait?
- **Sheet**: what function does the region serve, and will the change ripple style-wise into a follow-up pass?
- **Local**: what does the area look like when done; what alternative framing might the request really mean?

Then state and wait for approval:

1. **Goal** — what the design should do (design terms, not "add wires").
2. **Problem** — what's missing/wrong, citing specific symbols/nets/pins (via `sch query`).
3. **Approach** — proposed change + alternatives + why this one. Smallest unit that meets the goal.
4. **Placement** — for each new/moved symbol, which existing symbol or net it should sit *near* and why. Apply project electrical-design rules (grouping, partitioning, decoupling near supply pins).
5. **Layout sketch (ASCII art) — required** — show **both** the current and proposed local layout side-by-side. Coordinates are supplementary; the sketch is the primary artifact for the user to spot orientation/topology issues before any edit runs.

   ```
   Current:                Proposed:
        VCC                     VCC
         │                       │
         ●── U2.VCC               ●── U2.VCC
                                  │
                                 ─┴─ C99 100nF
                                  │
                                  ●── GND
   ```

Skip only if (a) the user gave an exact single action ("move R5 to 100,50"), or (b) running under an autonomous-iteration context the user explicitly invoked. When in doubt, plan and wait.

### 1. Pre-edit baseline

Routine edits — one-shot validate is enough:

```bash
kicad-tool sch validate <top> --sheet <edited-child>
```

Non-trivial structural edits (wire delete/replace, hierarchy changes) — snapshot a baseline first to confirm connectivity, not just "ERC still passes":

```bash
kicad-tool sch validate <top> --save-baseline tmp/baseline   # before
kicad-tool sch validate <top> --baseline tmp/baseline        # after
```

Baseline diff exits non-zero on regression (new ERC errors, removed nets, removed nodes from existing nets).

### 2. Edit (functional)

Smallest change satisfying the request. Always pass `--dry-run` first via `sch edit` / `pcb edit`, then re-run without it.

Scope of stage 1: connectivity, nets, symbols, pins, values, hierarchy, ERC correctness. Do **not** chase `sch inspect` score, collisions, or wire-corner counts here — that is stage 2.

If the new edit happens to introduce obvious local mess (overlapping label, crossing wire) that blocks reading the diff, fix only that local mess. Anything broader belongs to the cleanup loop.

### 3. Validate

Re-run the validate / baseline command from step 1. When running stages individually:

- **ERC** / **Netlist**: always against the **top-level** (hierarchical ERC propagates into children). Never on a child sheet directly.
- **Inspect**: each edited sheet.
- **PCB DRC**: only when the board was edited; pass `--schematic-parity` for parity check.

### 4. Review

- Existing ERC violations are not failures; **new** ones are. Intentional new violations (e.g. a deliberately-dangling test label) are acceptable — call them out in the report.
- Netlist diff: visual-only edits should differ only in path/header metadata. For electrical edits, summarize the intended changes.
- Successful CLI exit ≠ correctness. **ERC parity + expected netlist diff + score check (when relevant)** = high confidence.
- `git diff` review: confirm `Sheetfile` references, net names, and that custom symbol changes appear in both embedded `lib_symbols` and the standalone `.kicad_sym`.
- A worse `sch inspect` score is acceptable in stage 1 if connectivity is correct; do not chase it here.

### 5. Hand off to stage 2 (visual cleanup)

Stage 1 ends once ERC and netlist are correct. If the user wants score/layout tightening on the edited sheet, hand off to `kicad-sch-cleanup-loop` with the sheet path.

## Board changes

For `.kicad_pcb`:

- Keep stable: `layers`, `setup`, `setup.stackup`, `Edge.Cuts`, title block — unless the task explicitly targets them.
- High-risk fields: `net`, `footprint`, `segment`, `via`, `zone`.
- Run `pcb drc` after board edits; use `--schematic-parity` for schematic/board parity.
- Document known KiCad CLI limitations separately from true DRC violations.

Project-specific stackup and domain-separation rules live in the consuming repo's `hardware/pcb/` docs.

## Report format

```
ERC: pass (or: exit=N, same as before edit)
Netlist: intended changes only (or: identical / path-only diff)
Inspect: total=<score> collisions=<count>
PCB DRC: pass
```

On failure, include the failing stage, report path, and the first actionable issue:

```
FAIL: New ERC violation
  See tmp/erc.rpt
  First: ...
```
