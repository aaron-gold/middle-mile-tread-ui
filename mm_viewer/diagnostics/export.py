"""Per-scan Excel export: the algo-vs-LMI comparison tables AND the plots, together.

PURE: builds an .xlsx with openpyxl; no Qt. One workbook per scan:
  Summary      scan metadata + a per-axle / per-tyre roll-up
  <axle>       one sheet per axle: the per-groove comparison table AND the three
               rendered plots, side by side on the same sheet
  Methods      how the comparison is built, sign conventions, and the caveats

Plots are rendered off-screen with the Agg backend (independent of the live Qt canvas)
into the sheet as PNG images, so the file is self-contained and opens anywhere.
"""

import io
import os

from mm_viewer.common import MATPLOTLIB_AVAILABLE, Figure
from mm_viewer.diagnostics import plots
from mm_viewer.diagnostics.compare import axle_title, axle_key
from mm_viewer.diagnostics.loader import _finite


# table columns: (header, key-or-callable, number-format)
_COLS = [
    ("Groove", lambda r: r["label"], None),
    ("Tyre", lambda r: r["tire"], None),
    ("Edge dist (mm)", lambda r: r["wheel_edge_position"], "0.0"),
    ("Position (mm)", lambda r: r["position"], "0.0"),
    ("Algo raw (mm)", lambda r: r["algo_orig"], "0.00"),
    ("Algo 0.87× (mm)", lambda r: r["algo_corr"], "0.00"),
    ("Algo σ (mm)", lambda r: r["algo_std"], "0.00"),
    ("Algo n", lambda r: r["n_measurements"], "0"),
    ("LMI median (mm)", lambda r: r["lmi"]["median"], "0.00"),
    ("LMI mean (mm)", lambda r: r["lmi"]["mean"], "0.00"),
    ("LMI σ (mm)", lambda r: r["lmi"]["std"], "0.00"),
    ("LMI n", lambda r: r["lmi"]["n"], "0"),
    ("Δ raw (mm)", lambda r: r["d_orig"], "0.00"),
    ("Δ 0.87× (mm)", lambda r: r["d_corr"], "0.00"),
    ("Valid bottom", lambda r: "yes" if r["valid_bottom"] else "no", None),
    ("Occluded", lambda r: "yes" if r["occluded"] else "", None),
]

NAVY = "0F2A3F"
ORANGE = "E8791E"
GREY = "F3F6FA"
RED_SOFT = "F6D6D6"
AMBER_SOFT = "FCEFD2"
GREEN_SOFT = "D8F0DE"


def export_scan(scan_id, comparisons, out_path, scan_folder=None):
    """
    Write the Excel workbook for one scan.

    scan_id       the scan UUID / id string (goes on the Summary sheet).
    comparisons   [compare.compare_axle(...) dict, ...], already ordered.
    out_path      destination .xlsx (overwritten only by the caller's choice).
    Returns out_path. Raises on a real I/O/openpyxl error (the caller reports it).
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    thin = Side(style="thin", color="C8D2E0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    hdr_fill = PatternFill("solid", fgColor=NAVY)
    hdr_font = Font(color="FFFFFF", bold=True, size=11)
    title_font = Font(color=NAVY, bold=True, size=14)
    sub_font = Font(color="52627A", size=10)
    centre = Alignment(horizontal="center", vertical="center")

    # ---- Summary ----
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Middle Mile — Laser-groove diagnostics: algo vs LMI"
    ws["A1"].font = title_font
    ws["A2"] = "Scan: %s" % (scan_id or "—")
    ws["A2"].font = sub_font
    ws["A3"] = ("Algo depth 0.87× = algo raw × 0.87 (fixed, undocumented scale). "
                "LMI = firmware per-frame groove depth. Δ = algo − LMI. "
                "Inner (dual) grooves are occlusion-limited — treat as lower bounds.")
    ws["A3"].font = sub_font
    ws["A3"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells("A3:J4")

    head = ["Axle", "Tyre", "Grooves", "Matched", "Algo raw mean", "Algo 0.87× mean",
            "LMI median mean", "Mean |Δ raw|", "Mean |Δ 0.87×|", "Occluded"]
    hr = 6
    for c, h in enumerate(head, 1):
        cell = ws.cell(hr, c, h)
        cell.fill = hdr_fill; cell.font = hdr_font; cell.border = border
        cell.alignment = centre
    r = hr + 1
    for cmp in comparisons:
        for tire, s in cmp["tyres"].items():
            vals = [axle_title(cmp["axle"]), tire, s["n_grooves"], s["n_matched"],
                    s["algo_orig_mean"], s["algo_corr_mean"], s["lmi_median_mean"],
                    s["mean_abs_d_orig"], s["mean_abs_d_corr"],
                    "yes" if s["occluded"] else ""]
            for c, v in enumerate(vals, 1):
                cell = ws.cell(r, c, _num(v))
                cell.border = border
                if c >= 5 and c <= 9 and isinstance(cell.value, (int, float)):
                    cell.number_format = "0.00"
                if s["occluded"]:
                    cell.fill = PatternFill("solid", fgColor=AMBER_SOFT)
            r += 1
    _autoshape(ws, [16, 8, 9, 9, 14, 15, 15, 13, 14, 10])
    ws.freeze_panes = "A7"

    # ---- one sheet per axle ----
    used = set()
    for cmp in comparisons:
        name = _unique(axle_key(cmp["axle"]), used)
        sh = wb.create_sheet(name)
        _axle_sheet(sh, cmp, border, hdr_fill, hdr_font, title_font, sub_font, centre)

    # ---- Methods ----
    _methods_sheet(wb.create_sheet("Methods"), title_font, sub_font, scan_id,
                   scan_folder)

    out_path = os.path.abspath(out_path)
    wb.save(out_path)
    return out_path


def _axle_sheet(ws, cmp, border, hdr_fill, hdr_font, title_font, sub_font, centre):
    from openpyxl.styles import PatternFill, Alignment, Font
    a = cmp["axle"]
    ws["A1"] = axle_title(a)
    ws["A1"].font = title_font
    ws["A2"] = plots._subtitle(cmp)
    ws["A2"].font = sub_font
    ws.merge_cells("A2:P2")

    hr = 4
    for c, (h, _f, _nf) in enumerate(_COLS, 1):
        cell = ws.cell(hr, c, h)
        cell.fill = hdr_fill; cell.font = hdr_font; cell.border = border
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
    r = hr + 1
    for row in cmp["grooves"]:
        for c, (_h, fn, nf) in enumerate(_COLS, 1):
            v = fn(row)
            cell = ws.cell(r, c, _num(v))
            cell.border = border
            if nf and isinstance(cell.value, (int, float)):
                cell.number_format = nf
            # delta colouring (cols 13,14): green near 0, amber <2, red >=2
            if c in (13, 14) and _finite(v):
                av = abs(v)
                fill = (GREEN_SOFT if av < 0.5 else AMBER_SOFT if av < 2.0 else RED_SOFT)
                cell.fill = PatternFill("solid", fgColor=fill)
        if row["occluded"]:
            ws.cell(r, 16).fill = PatternFill("solid", fgColor=AMBER_SOFT)
        r += 1
    _autoshape(ws, [9, 7, 12, 12, 12, 13, 10, 8, 13, 12, 10, 7, 11, 12, 11, 9])
    ws.freeze_panes = "A5"

    if not MATPLOTLIB_AVAILABLE:
        return
    # plots, below the table, one under another on the SAME sheet
    anchor_row = r + 2
    for draw, figsize in ((plots.draw_depth_vs_position, (8.2, 4.2)),
                          (plots.draw_delta_bars, (8.2, 3.6)),
                          (plots.draw_raw_scatter, (8.2, 3.6))):
        img = _fig_image(draw, cmp, figsize)
        if img is not None:
            ws.add_image(img, "A%d" % anchor_row)
            anchor_row += 22


def _fig_image(draw_fn, cmp, figsize):
    """Render a draw function to an openpyxl image (off-screen Agg)."""
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from openpyxl.drawing.image import Image as XLImage
        fig = Figure(figsize=figsize, dpi=120)
        FigureCanvasAgg(fig)
        draw_fn(fig, cmp)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
        buf.seek(0)
        img = XLImage(buf)
        img._buf = buf          # keep the buffer alive until the workbook is saved
        return img
    except Exception:
        return None


def _methods_sheet(ws, title_font, sub_font, scan_id, scan_folder):
    ws["A1"] = "Method & caveats"
    ws["A1"].font = title_font
    lines = [
        "",
        "Scan id: %s" % (scan_id or "—"),
        "Source: %s" % (scan_folder or "—"),
        "",
        "ALGO depths come from diagnostics/laser_tread/<LMI unit>/truck_axle<k>/"
        "*_finalized_groove_depths.csv.",
        "  • 'Algo raw' = |median_original_depth|.",
        "  • 'Algo 0.87×' = |median_depth| = algo raw × 0.87 (a fixed, undocumented "
        "scale, not a tilt correction).",
        "",
        "LMI depths are the firmware's own per-frame groove depths from "
        "tire_laser_groove_data/<LMI unit>/frame_<N>_grooves.csv (Depth column).",
        "  • Only the frame window the algo used for that axle "
        "(request.json FirstFrameIndex..LastFrameIndex) is read.",
        "  • The raw grooves drift laterally frame to frame (the tyre translates), so "
        "each frame is registered onto the",
        "    algo groove positions by the single lateral offset that aligns the most "
        "grooves; a frame that aligns too few",
        "    grooves is dropped (counted on each sheet).",
        "",
        "Matching is by lateral position (G1 = nearest wheel edge where the algo "
        "reports a wheel-edge distance; the",
        "FRONT_RIGHT / LMI_1 units leave it blank, so those are ordered by position). "
        "Never by raw slot index.",
        "",
        "CAVEATS (do not read these as acceptance):",
        "  • The LMI firmware depth tracks the ALGO RAW depth; the 0.87× depth reads "
        "~1–1.5 mm lower on steer tyres.",
        "  • DUAL (inner) grooves are occlusion-limited — the groove bottom is outside "
        "the laser's line of sight, so a",
        "    dual reading is a LOWER BOUND, not a depth. Dual Δ values are repeatability "
        "evidence only.",
        "  • Ground-truth gauge depths are NOT in this file; this is algo-vs-LMI, not "
        "accuracy-vs-gauge.",
    ]
    for i, t in enumerate(lines, 2):
        ws.cell(i, 1, t).font = sub_font
    ws.column_dimensions["A"].width = 118


def _num(v):
    """None -> '', numbers pass through, everything else -> str."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        import math
        return "" if not math.isfinite(v) else round(float(v), 4)
    return str(v)


def _autoshape(ws, widths):
    from openpyxl.utils import get_column_letter
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _unique(name, used):
    name = name[:31]
    base, n = name, 2
    while name.lower() in used:
        name = ("%s_%d" % (base[:28], n))
        n += 1
    used.add(name.lower())
    return name
