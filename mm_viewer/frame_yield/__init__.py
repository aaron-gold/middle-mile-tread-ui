"""Frame-yield feature (v1.13): how many LMI frames were assigned per wheel/axle and
how many actually carry a groove measurement. Shared by the Laser Grooves (LMI) tab and
the Diagnostics tab, for the 8 Hz vs 4 Hz laser-window comparison.

  compute   pure data layer (per LMI unit, and per axle x wheel)
  plot      matplotlib gauge (single scan) and side-by-side compare
  panel     the Qt panel (Group / Axle selectors + Compare scan)
"""
