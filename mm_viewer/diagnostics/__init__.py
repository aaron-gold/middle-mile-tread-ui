"""Diagnostics - the laser-groove ALGO output and its comparison with the raw LMI.

New in MM Viewer v1.11 (2026-11-05). A scan processed by the improved laser-groove
module writes a `diagnostics/laser_tread/LMI_<n>_<POS>/truck_axle<k>/` tree: per LMI
sensor, per axle it saw, the algo's finalized groove depths (CSV) and a set of
step-by-step visualisations (PNG). This package reads that tree, compares the algo's
depths against the LMI firmware's own per-frame groove depths, and shows both as
tables + plots in a dedicated "Diagnostics" sub-tab, with a per-scan Excel export.

Modules (one concern each, pure data first, Qt last):
  loader    parse the diagnostics tree + the raw LMI frame grooves              (pure)
  compare   register raw frames onto algo groove positions, build the compare   (pure)
  plots     matplotlib draw functions, reused by the panel and the export       (pure)
  export    per-scan Excel workbook (tables + embedded plots)                    (pure)
  worker    off-GUI-thread parse/compare                                        (QThread)
  images    browse the algo's diagnostic PNGs                                   (Qt)
  stitched  show a stitched side image if one is present in the scan            (Qt)
  panel     the comparison panel (selectors, canvas, table, export)             (Qt)
  tab       the Diagnostics sub-tab (Comparison / Algo images)                  (Qt)
"""
