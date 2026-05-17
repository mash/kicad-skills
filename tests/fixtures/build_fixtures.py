#!/usr/bin/env python3
"""Generate minimal KiCad fixture files for the test suite.

We emit raw S-expression text rather than going through ``kiutils``'s
object model, because constructing a brand-new schematic with proper
``lib_symbols`` from scratch via kiutils is awkward and fragile across
versions. The output here is intentionally hand-crafted, minimal, and
self-contained:

- ``minimal.kicad_sch`` defines two private library symbols
  (``minimal:R`` and ``minimal:LED``), each with two pins, then places
  ``R1`` and ``LED1`` on the sheet, connects them with a couple of
  wires, drops a junction, a local label, and a global label.
- ``minimal.kicad_pcb`` is a near-minimal KiCad 9/10 board (layers +
  setup only) plus two SMD footprint blocks for ``R1`` / ``LED1`` and a
  single ``segment`` track on ``F.Cu``.
- ``minimal.kicad_pro`` is the smallest project file the loader will
  accept.

The script is idempotent: re-run it to regenerate files. We use fixed
UUIDs so the fixtures are diff-stable.
"""
from __future__ import annotations

from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent


SCH_TEMPLATE = """(kicad_sch
\t(version 20260306)
\t(generator "eeschema")
\t(generator_version "10.0")
\t(uuid "00000000-0000-0000-0000-0000000000aa")
\t(paper "A4")
\t(title_block
\t\t(title "minimal")
\t\t(company "test-fixture")
\t)
\t(lib_symbols
\t\t(symbol "minimal:R"
\t\t\t(pin_names
\t\t\t\t(offset 0.762)
\t\t\t)
\t\t\t(exclude_from_sim no)
\t\t\t(in_bom yes)
\t\t\t(on_board yes)
\t\t\t(duplicate_pin_numbers_are_jumpers no)
\t\t\t(property "Reference" "R"
\t\t\t\t(at 0 3.81 0)
\t\t\t\t(effects
\t\t\t\t\t(font
\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t\t(property "Value" "R"
\t\t\t\t(at 0 -3.81 0)
\t\t\t\t(effects
\t\t\t\t\t(font
\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t\t(property "Footprint" ""
\t\t\t\t(at 0 0 0)
\t\t\t\t(hide yes)
\t\t\t\t(effects
\t\t\t\t\t(font
\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t\t(property "Datasheet" ""
\t\t\t\t(at 0 0 0)
\t\t\t\t(hide yes)
\t\t\t\t(effects
\t\t\t\t\t(font
\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t\t(property "Description" "Generic resistor (fixture)"
\t\t\t\t(at 0 0 0)
\t\t\t\t(hide yes)
\t\t\t\t(effects
\t\t\t\t\t(font
\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t\t(symbol "R_0_1"
\t\t\t\t(rectangle
\t\t\t\t\t(start -1.016 2.54)
\t\t\t\t\t(end 1.016 -2.54)
\t\t\t\t\t(stroke
\t\t\t\t\t\t(width 0.254)
\t\t\t\t\t\t(type default)
\t\t\t\t\t)
\t\t\t\t\t(fill
\t\t\t\t\t\t(type none)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t\t(symbol "R_1_1"
\t\t\t\t(pin passive line
\t\t\t\t\t(at 0 5.08 270)
\t\t\t\t\t(length 2.54)
\t\t\t\t\t(name "~"
\t\t\t\t\t\t(effects
\t\t\t\t\t\t\t(font
\t\t\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t\t\t)
\t\t\t\t\t\t)
\t\t\t\t\t)
\t\t\t\t\t(number "1"
\t\t\t\t\t\t(effects
\t\t\t\t\t\t\t(font
\t\t\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t\t\t)
\t\t\t\t\t\t)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t\t(pin passive line
\t\t\t\t\t(at 0 -5.08 90)
\t\t\t\t\t(length 2.54)
\t\t\t\t\t(name "~"
\t\t\t\t\t\t(effects
\t\t\t\t\t\t\t(font
\t\t\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t\t\t)
\t\t\t\t\t\t)
\t\t\t\t\t)
\t\t\t\t\t(number "2"
\t\t\t\t\t\t(effects
\t\t\t\t\t\t\t(font
\t\t\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t\t\t)
\t\t\t\t\t\t)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t\t(embedded_fonts no)
\t\t)
\t\t(symbol "minimal:LED"
\t\t\t(pin_names
\t\t\t\t(offset 1.016)
\t\t\t\t(hide yes)
\t\t\t)
\t\t\t(exclude_from_sim no)
\t\t\t(in_bom yes)
\t\t\t(on_board yes)
\t\t\t(duplicate_pin_numbers_are_jumpers no)
\t\t\t(property "Reference" "D"
\t\t\t\t(at 0 2.54 0)
\t\t\t\t(effects
\t\t\t\t\t(font
\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t\t(property "Value" "LED"
\t\t\t\t(at 0 -2.54 0)
\t\t\t\t(effects
\t\t\t\t\t(font
\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t\t(property "Footprint" ""
\t\t\t\t(at 0 0 0)
\t\t\t\t(hide yes)
\t\t\t\t(effects
\t\t\t\t\t(font
\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t\t(property "Datasheet" ""
\t\t\t\t(at 0 0 0)
\t\t\t\t(hide yes)
\t\t\t\t(effects
\t\t\t\t\t(font
\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t\t(property "Description" "Generic LED (fixture)"
\t\t\t\t(at 0 0 0)
\t\t\t\t(hide yes)
\t\t\t\t(effects
\t\t\t\t\t(font
\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t\t(symbol "LED_0_1"
\t\t\t\t(polyline
\t\t\t\t\t(pts
\t\t\t\t\t\t(xy -1.27 -1.27) (xy -1.27 1.27)
\t\t\t\t\t)
\t\t\t\t\t(stroke
\t\t\t\t\t\t(width 0.254)
\t\t\t\t\t\t(type default)
\t\t\t\t\t)
\t\t\t\t\t(fill
\t\t\t\t\t\t(type none)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t\t(symbol "LED_1_1"
\t\t\t\t(pin passive line
\t\t\t\t\t(at -3.81 0 0)
\t\t\t\t\t(length 2.54)
\t\t\t\t\t(name "K"
\t\t\t\t\t\t(effects
\t\t\t\t\t\t\t(font
\t\t\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t\t\t)
\t\t\t\t\t\t)
\t\t\t\t\t)
\t\t\t\t\t(number "1"
\t\t\t\t\t\t(effects
\t\t\t\t\t\t\t(font
\t\t\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t\t\t)
\t\t\t\t\t\t)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t\t(pin passive line
\t\t\t\t\t(at 3.81 0 180)
\t\t\t\t\t(length 2.54)
\t\t\t\t\t(name "A"
\t\t\t\t\t\t(effects
\t\t\t\t\t\t\t(font
\t\t\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t\t\t)
\t\t\t\t\t\t)
\t\t\t\t\t)
\t\t\t\t\t(number "2"
\t\t\t\t\t\t(effects
\t\t\t\t\t\t\t(font
\t\t\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t\t\t)
\t\t\t\t\t\t)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t\t(embedded_fonts no)
\t\t)
\t)
\t(junction
\t\t(at 50.8 60.96)
\t\t(diameter 0)
\t\t(color 0 0 0 0)
\t\t(uuid "00000000-0000-0000-0000-0000000000b1")
\t)
\t(wire
\t\t(pts
\t\t\t(xy 50.8 50.8) (xy 50.8 60.96)
\t\t)
\t\t(stroke
\t\t\t(width 0)
\t\t\t(type default)
\t\t)
\t\t(uuid "00000000-0000-0000-0000-0000000000c1")
\t)
\t(wire
\t\t(pts
\t\t\t(xy 50.8 60.96) (xy 60.96 60.96)
\t\t)
\t\t(stroke
\t\t\t(width 0)
\t\t\t(type default)
\t\t)
\t\t(uuid "00000000-0000-0000-0000-0000000000c2")
\t)
\t(label "NET1"
\t\t(at 55.88 60.96 0)
\t\t(effects
\t\t\t(font
\t\t\t\t(size 1.27 1.27)
\t\t\t)
\t\t\t(justify left bottom)
\t\t)
\t\t(uuid "00000000-0000-0000-0000-0000000000d1")
\t)
\t(global_label "+3V3"
\t\t(shape input)
\t\t(at 50.8 45.72 0)
\t\t(effects
\t\t\t(font
\t\t\t\t(size 1.27 1.27)
\t\t\t)
\t\t\t(justify left)
\t\t)
\t\t(uuid "00000000-0000-0000-0000-0000000000d2")
\t\t(property "Intersheetrefs" "${INTERSHEET_REFS}"
\t\t\t(at 50.8 45.72 0)
\t\t\t(hide yes)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t)
\t(symbol
\t\t(lib_id "minimal:R")
\t\t(at 50.8 55.88 0)
\t\t(unit 1)
\t\t(body_style 1)
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(in_pos_files yes)
\t\t(dnp no)
\t\t(uuid "00000000-0000-0000-0000-0000000000e1")
\t\t(property "Reference" "R1"
\t\t\t(at 53.34 53.34 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Value" "10k"
\t\t\t(at 53.34 58.42 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Footprint" "Resistor_SMD:R_0603_1608Metric"
\t\t\t(at 50.8 55.88 0)
\t\t\t(hide yes)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Datasheet" "~"
\t\t\t(at 50.8 55.88 0)
\t\t\t(hide yes)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(pin "1"
\t\t\t(uuid "00000000-0000-0000-0000-0000000000e2")
\t\t)
\t\t(pin "2"
\t\t\t(uuid "00000000-0000-0000-0000-0000000000e3")
\t\t)
\t\t(instances
\t\t\t(project "minimal"
\t\t\t\t(path "/00000000-0000-0000-0000-0000000000aa"
\t\t\t\t\t(reference "R1")
\t\t\t\t\t(unit 1)
\t\t\t\t)
\t\t\t)
\t\t)
\t)
\t(symbol
\t\t(lib_id "minimal:LED")
\t\t(at 71.12 60.96 0)
\t\t(unit 1)
\t\t(body_style 1)
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(in_pos_files yes)
\t\t(dnp no)
\t\t(uuid "00000000-0000-0000-0000-0000000000f1")
\t\t(property "Reference" "LED1"
\t\t\t(at 71.12 57.15 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Value" "LED"
\t\t\t(at 71.12 63.5 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Footprint" "LED_SMD:LED_0603_1608Metric"
\t\t\t(at 71.12 60.96 0)
\t\t\t(hide yes)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Datasheet" "~"
\t\t\t(at 71.12 60.96 0)
\t\t\t(hide yes)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(pin "1"
\t\t\t(uuid "00000000-0000-0000-0000-0000000000f2")
\t\t)
\t\t(pin "2"
\t\t\t(uuid "00000000-0000-0000-0000-0000000000f3")
\t\t)
\t\t(instances
\t\t\t(project "minimal"
\t\t\t\t(path "/00000000-0000-0000-0000-0000000000aa"
\t\t\t\t\t(reference "LED1")
\t\t\t\t\t(unit 1)
\t\t\t\t)
\t\t\t)
\t\t)
\t)
\t(sheet_instances
\t\t(path "/"
\t\t\t(page "1")
\t\t)
\t)
\t(embedded_fonts no)
)
"""


PCB_TEMPLATE = """(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(generator_version "10.0")
\t(general
\t\t(thickness 1.6)
\t\t(legacy_teardrops no)
\t)
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
\t\t(11 "B.Adhes" user "B.Adhesive")
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(17 "Dwgs.User" user "User.Drawings")
\t\t(19 "Cmts.User" user "User.Comments")
\t\t(21 "Eco1.User" user "User.Eco1")
\t\t(23 "Eco2.User" user "User.Eco2")
\t\t(25 "Edge.Cuts" user)
\t\t(27 "Margin" user)
\t\t(31 "F.CrtYd" user "F.Courtyard")
\t\t(29 "B.CrtYd" user "B.Courtyard")
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)
\t(setup
\t\t(pad_to_mask_clearance 0)
\t)
\t(net 0 "")
\t(net 1 "NET1")
\t(footprint "Resistor_SMD:R_0603_1608Metric"
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-00000000aa01")
\t\t(at 100.0 100.0 0)
\t\t(property "Reference" "R1"
\t\t\t(at 0 -1.43 0)
\t\t\t(layer "F.SilkS")
\t\t\t(uuid "00000000-0000-0000-0000-00000000aa02")
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1 1)
\t\t\t\t\t(thickness 0.15)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Value" "10k"
\t\t\t(at 0 1.43 0)
\t\t\t(layer "F.Fab")
\t\t\t(uuid "00000000-0000-0000-0000-00000000aa03")
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1 1)
\t\t\t\t\t(thickness 0.15)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(attr smd)
\t\t(pad "1" smd roundrect
\t\t\t(at -0.825 0)
\t\t\t(size 0.8 0.95)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t\t(roundrect_rratio 0.25)
\t\t\t(net 1 "NET1")
\t\t\t(uuid "00000000-0000-0000-0000-00000000aa04")
\t\t)
\t\t(pad "2" smd roundrect
\t\t\t(at 0.825 0)
\t\t\t(size 0.8 0.95)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t\t(roundrect_rratio 0.25)
\t\t\t(uuid "00000000-0000-0000-0000-00000000aa05")
\t\t)
\t\t(embedded_fonts no)
\t)
\t(footprint "LED_SMD:LED_0603_1608Metric"
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-00000000bb01")
\t\t(at 110.0 100.0 0)
\t\t(property "Reference" "LED1"
\t\t\t(at 0 -1.43 0)
\t\t\t(layer "F.SilkS")
\t\t\t(uuid "00000000-0000-0000-0000-00000000bb02")
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1 1)
\t\t\t\t\t(thickness 0.15)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Value" "LED"
\t\t\t(at 0 1.43 0)
\t\t\t(layer "F.Fab")
\t\t\t(uuid "00000000-0000-0000-0000-00000000bb03")
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1 1)
\t\t\t\t\t(thickness 0.15)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(attr smd)
\t\t(pad "1" smd roundrect
\t\t\t(at -0.825 0)
\t\t\t(size 0.8 0.95)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t\t(roundrect_rratio 0.25)
\t\t\t(net 1 "NET1")
\t\t\t(uuid "00000000-0000-0000-0000-00000000bb04")
\t\t)
\t\t(pad "2" smd roundrect
\t\t\t(at 0.825 0)
\t\t\t(size 0.8 0.95)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t\t(roundrect_rratio 0.25)
\t\t\t(uuid "00000000-0000-0000-0000-00000000bb05")
\t\t)
\t\t(embedded_fonts no)
\t)
\t(segment
\t\t(start 100.825 100.0)
\t\t(end 109.175 100.0)
\t\t(width 0.2)
\t\t(layer "F.Cu")
\t\t(net 1)
\t\t(uuid "00000000-0000-0000-0000-00000000cc01")
\t)
\t(embedded_fonts no)
)
"""


PRO_TEMPLATE = """{
  "meta": {
    "filename": "minimal.kicad_pro",
    "version": 1
  }
}
"""


ZONES_PRO_TEMPLATE = """{
  "meta": {
    "filename": "minimal_zones.kicad_pro",
    "version": 1
  }
}
"""


VIAS_PRO_TEMPLATE = """{
  "meta": {
    "filename": "minimal_vias.kicad_pro",
    "version": 1
  }
}
"""


PCB_VIAS_TEMPLATE = """(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(generator_version "10.0")
\t(general
\t\t(thickness 1.6)
\t\t(legacy_teardrops no)
\t)
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
\t\t(11 "B.Adhes" user "B.Adhesive")
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(17 "Dwgs.User" user "User.Drawings")
\t\t(19 "Cmts.User" user "User.Comments")
\t\t(21 "Eco1.User" user "User.Eco1")
\t\t(23 "Eco2.User" user "User.Eco2")
\t\t(25 "Edge.Cuts" user)
\t\t(27 "Margin" user)
\t\t(31 "F.CrtYd" user "F.Courtyard")
\t\t(29 "B.CrtYd" user "B.Courtyard")
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)
\t(setup
\t\t(pad_to_mask_clearance 0)
\t\t(via_size 0.8)
\t\t(via_drill 0.4)
\t)
\t(net 0 "")
\t(net 1 "GND")
\t(net 2 "VCC")
\t(segment
\t\t(start 100.0 100.0)
\t\t(end 110.0 100.0)
\t\t(width 0.2)
\t\t(layer "F.Cu")
\t\t(net 1)
\t\t(uuid "00000000-0000-0000-0000-00000000cc01")
\t)
\t(via
\t\t(at 100.0 100.0)
\t\t(size 0.8)
\t\t(drill 0.4)
\t\t(layers "F.Cu" "B.Cu")
\t\t(net "GND")
\t\t(uuid "00000000-0000-0000-0000-0000000030a1")
\t)
\t(via
\t\t(at 110.0 100.0)
\t\t(size 0.6)
\t\t(drill 0.3)
\t\t(layers "F.Cu" "B.Cu")
\t\t(free yes)
\t\t(net "VCC")
\t\t(uuid "00000000-0000-0000-0000-0000000030a2")
\t)
\t(embedded_fonts no)
)
"""


PCB_ZONES_TEMPLATE = """(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(generator_version "10.0")
\t(general
\t\t(thickness 1.6)
\t\t(legacy_teardrops no)
\t)
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
\t\t(11 "B.Adhes" user "B.Adhesive")
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(17 "Dwgs.User" user "User.Drawings")
\t\t(19 "Cmts.User" user "User.Comments")
\t\t(21 "Eco1.User" user "User.Eco1")
\t\t(23 "Eco2.User" user "User.Eco2")
\t\t(25 "Edge.Cuts" user)
\t\t(27 "Margin" user)
\t\t(31 "F.CrtYd" user "F.Courtyard")
\t\t(29 "B.CrtYd" user "B.Courtyard")
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)
\t(setup
\t\t(pad_to_mask_clearance 0)
\t)
\t(net 0 "")
\t(net 1 "GND")
\t(net 2 "VCC")
\t(zone
\t\t(net 1)
\t\t(net_name "GND")
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-00000000201e")
\t\t(name "GND_TOP")
\t\t(hatch edge 0.5)
\t\t(priority 1)
\t\t(connect_pads yes
\t\t\t(clearance 0.5)
\t\t)
\t\t(min_thickness 0.25)
\t\t(fill yes
\t\t\t(thermal_gap 0.5)
\t\t\t(thermal_bridge_width 0.5)
\t\t\t(island_removal_mode 0)
\t\t)
\t\t(polygon
\t\t\t(pts
\t\t\t\t(xy 90.0 90.0) (xy 120.0 90.0) (xy 120.0 110.0) (xy 90.0 110.0)
\t\t\t)
\t\t)
\t\t(filled_polygon
\t\t\t(layer "F.Cu")
\t\t\t(pts
\t\t\t\t(xy 90.1 90.1) (xy 119.9 90.1) (xy 119.9 109.9) (xy 90.1 109.9)
\t\t\t)
\t\t)
\t)
\t(zone
\t\t(net 1)
\t\t(net_name "GND")
\t\t(layer "B.Cu")
\t\t(uuid "00000000-0000-0000-0000-00000000202e")
\t\t(name "GND_TOP")
\t\t(hatch edge 0.5)
\t\t(priority 1)
\t\t(connect_pads yes
\t\t\t(clearance 0.5)
\t\t)
\t\t(min_thickness 0.25)
\t\t(fill yes
\t\t\t(thermal_gap 0.5)
\t\t\t(thermal_bridge_width 0.5)
\t\t\t(island_removal_mode 0)
\t\t)
\t\t(polygon
\t\t\t(pts
\t\t\t\t(xy 90.0 90.0) (xy 120.0 90.0) (xy 120.0 110.0) (xy 90.0 110.0)
\t\t\t)
\t\t)
\t\t(filled_polygon
\t\t\t(layer "B.Cu")
\t\t\t(pts
\t\t\t\t(xy 90.1 90.1) (xy 119.9 90.1) (xy 119.9 109.9) (xy 90.1 109.9)
\t\t\t)
\t\t)
\t)
\t(zone
\t\t(net 2)
\t\t(net_name "VCC")
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-00000000203e")
\t\t(name "VCC_POUR")
\t\t(hatch edge 0.4)
\t\t(priority 10)
\t\t(connect_pads yes
\t\t\t(clearance 0.4)
\t\t)
\t\t(min_thickness 0.2)
\t\t(fill yes
\t\t\t(thermal_gap 0.4)
\t\t\t(thermal_bridge_width 0.4)
\t\t\t(island_removal_mode 0)
\t\t)
\t\t(polygon
\t\t\t(pts
\t\t\t\t(xy 95.0 95.0) (xy 105.0 95.0) (xy 105.0 105.0) (xy 95.0 105.0)
\t\t\t)
\t\t)
\t\t(filled_polygon
\t\t\t(layer "F.Cu")
\t\t\t(pts
\t\t\t\t(xy 95.1 95.1) (xy 104.9 95.1) (xy 104.9 104.9) (xy 95.1 104.9)
\t\t\t)
\t\t)
\t)
\t(embedded_fonts no)
)
"""


def main() -> None:
    (FIXTURES_DIR / "minimal.kicad_sch").write_text(SCH_TEMPLATE)
    (FIXTURES_DIR / "minimal.kicad_pcb").write_text(PCB_TEMPLATE)
    (FIXTURES_DIR / "minimal.kicad_pro").write_text(PRO_TEMPLATE)
    (FIXTURES_DIR / "minimal_zones.kicad_pcb").write_text(PCB_ZONES_TEMPLATE)
    (FIXTURES_DIR / "minimal_zones.kicad_pro").write_text(ZONES_PRO_TEMPLATE)
    (FIXTURES_DIR / "minimal_vias.kicad_pcb").write_text(PCB_VIAS_TEMPLATE)
    (FIXTURES_DIR / "minimal_vias.kicad_pro").write_text(VIAS_PRO_TEMPLATE)
    print(f"wrote fixtures to {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
