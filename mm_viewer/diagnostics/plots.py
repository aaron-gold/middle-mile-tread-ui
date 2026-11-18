"""Matplotlib draw functions for the algo-vs-LMI comparison.

PURE drawing: each function takes a `Figure` and a comparison dict (from
compare.compare_axle) and draws onto it. No canvas.draw(), no savefig - the caller
(the embedded panel, or the Excel export) owns the Figure's lifecycle. Guard on
MATPLOTLIB_AVAILABLE before calling.

Three views, plus a stacked overview used by the embedded canvas:
  draw_depth_vs_position   depth vs lateral position, algo(corr/orig) + LMI per tyre
  draw_delta_bars          per-groove algo-minus-LMI, both the raw and 0.87x depth
  draw_raw_scatter         per-groove spread of the raw LMI readings vs the algo value
  draw_overview            depth-vs-position over delta-bars, one figure (the panel)

Colours come from theme; the algo CORRECTED depth is orange (the app's accent), the
algo ORIGINAL depth blue, and the LMI firmware depth navy, everywhere, so the three
series read the same in every plot and in the exported sheet.
"""

from mm_viewer.common import MATPLOTLIB_AVAILABLE
from mm_viewer.diagnostics.loader import _finite
from mm_viewer.diagnostics.compare import axle_title
from mm_viewer.theme import (
    C_BLUE, C_NAVY, C_ORANGE, C_RED, C_TEXT_DIM, C_CYAN
)


C_CORR = C_ORANGE      # algo depth, 0.87x-scaled (the app accent)
C_ORIG = C_BLUE        # algo depth, raw (before 0.87x)
C_LMI = C_NAVY         # LMI firmware depth
C_POS = "#2f9e44"      # positive delta
C_NEG = C_RED          # negative delta
GRID = "#d6deea"


def _no_data(fig, msg):
    fig.clear()
    ax = fig.add_subplot(111)
    ax.axis("off")
    ax.text(0.5, 0.5, msg, ha="center", va="center", wrap=True,
            fontsize=11, color=C_TEXT_DIM)


def _subtitle(cmp):
    a = cmp["axle"]
    bits = []
    if cmp.get("has_raw"):
        bits.append("LMI frames %d used / %d dropped (window %s–%s)" % (
            cmp["n_frames_used"], cmp["n_frames_dropped"],
            a.get("first_frame"), a.get("last_frame")))
    else:
        bits.append("no raw LMI frames found — algo output only")
    bits.append("match ±%.0f mm" % cmp.get("tol", 0))
    return "   ·   ".join(bits)


def _tyre_rows(cmp):
    """Grooves grouped by tyre, each tyre's grooves in G-order (label order)."""
    groups = {}
    for r in cmp["grooves"]:
        groups.setdefault(r["tire"] or "tyre", []).append(r)
    for rs in groups.values():
        rs.sort(key=lambda r: _label_rank(r["label"]))
    # outer first, then inner, then anything else
    order = sorted(groups, key=lambda t: (t != "outer", t != "inner", t))
    return [(t, groups[t]) for t in order]


def _label_rank(label):
    import re
    m = re.search(r"G(\d+)", label or "")
    return int(m.group(1)) if m else 0


# ----------------------------------------------------------------------------
def draw_depth_vs_position(fig, cmp, ax=None):
    """Depth (mm) vs lateral position (mm): algo corrected, algo original, LMI."""
    if not MATPLOTLIB_AVAILABLE:
        return
    rows = cmp.get("grooves") or []
    if not rows:
        _no_data(fig, "No algo grooves for this axle.")
        return
    own = ax is None
    if own:
        fig.clear()
        ax = fig.add_subplot(111)

    xs = [r["position"] for r in rows if _finite(r["position"])]
    # algo series (always present)
    _scatter_line(ax, rows, "position", "algo_orig", C_ORIG, "Algo depth (raw)", "o")
    _scatter_line(ax, rows, "position", "algo_corr", C_CORR,
                  "Algo depth (0.87×)", "s")
    # LMI series with spread
    lx, ly, lerr = [], [], []
    for r in rows:
        if _finite(r["position"]) and r["lmi"]["n"] > 0 and _finite(r["lmi"]["median"]):
            lx.append(r["position"]); ly.append(r["lmi"]["median"])
            lerr.append(r["lmi"]["std"] or 0.0)
    if lx:
        ax.errorbar(lx, ly, yerr=lerr, fmt="D", ms=6, color=C_LMI, ecolor=C_LMI,
                    elinewidth=1, capsize=3, label="LMI firmware (median±σ)",
                    linestyle="none", zorder=5)
    # groove labels + occlusion shading
    _annotate_labels(ax, rows, "position", "algo_orig")
    _shade_occluded(ax, rows, "position")

    ax.set_xlabel("Lateral position  (mm)")
    ax.set_ylabel("Groove depth  (mm)")
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.margins(x=0.08)
    if own:
        ax.set_title("%s — depth vs position" % axle_title(cmp["axle"]),
                     fontsize=12, color=C_NAVY)
        fig.text(0.5, 0.005, _subtitle(cmp), ha="center", fontsize=7.5,
                 color=C_TEXT_DIM)
    _legend(ax)
    _tighten(fig, own)


def draw_delta_bars(fig, cmp, ax=None):
    """Per-groove algo-minus-LMI, for the raw and the 0.87× algo depth."""
    if not MATPLOTLIB_AVAILABLE:
        return
    rows = [r for r in (cmp.get("grooves") or []) if r["lmi"]["n"] > 0]
    own = ax is None
    if own:
        fig.clear()
        ax = fig.add_subplot(111)
    if not rows:
        _no_data(fig, "No groove matched a raw LMI reading, so no Δ can be shown.")
        return
    import numpy as np
    labels = [r["label"] for r in rows]
    d_orig = [r["d_orig"] if _finite(r["d_orig"]) else 0.0 for r in rows]
    d_corr = [r["d_corr"] if _finite(r["d_corr"]) else 0.0 for r in rows]
    x = np.arange(len(rows))
    w = 0.38
    ax.bar(x - w / 2, d_orig, w, color=C_ORIG, label="Algo raw − LMI")
    ax.bar(x + w / 2, d_corr, w, color=C_CORR, label="Algo 0.87× − LMI")
    ax.axhline(0, color=C_NAVY, linewidth=0.8)
    for i, r in enumerate(rows):
        if r["occluded"]:
            ax.axvspan(i - 0.5, i + 0.5, color=C_RED, alpha=0.06, zorder=0)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=0, fontsize=8)
    ax.set_ylabel("Algo − LMI  (mm)")
    ax.grid(True, axis="y", color=GRID, linewidth=0.6)
    if own:
        ax.set_title("%s — algo minus LMI per groove" % axle_title(cmp["axle"]),
                     fontsize=12, color=C_NAVY)
    _legend(ax)
    _tighten(fig, own)


def draw_raw_scatter(fig, cmp, ax=None):
    """Per-groove spread of the raw LMI readings, with the algo depths marked."""
    if not MATPLOTLIB_AVAILABLE:
        return
    rows = cmp.get("grooves") or []
    own = ax is None
    if own:
        fig.clear()
        ax = fig.add_subplot(111)
    if not rows:
        _no_data(fig, "No algo grooves for this axle.")
        return
    import numpy as np
    labels = [r["label"] for r in rows]
    for i, r in enumerate(rows):
        vals = [v for v in r["lmi_values"] if _finite(v)]
        if vals:
            jitter = (np.random.default_rng(i).random(len(vals)) - 0.5) * 0.28
            ax.scatter(np.full(len(vals), i) + jitter, vals, s=16, color=C_LMI,
                       alpha=0.55, zorder=3,
                       label="LMI readings" if i == 0 else None)
        if _finite(r["algo_orig"]):
            ax.scatter([i], [r["algo_orig"]], marker="o", s=55, color=C_ORIG,
                       edgecolor="white", zorder=5,
                       label="Algo raw" if i == 0 else None)
        if _finite(r["algo_corr"]):
            ax.scatter([i], [r["algo_corr"]], marker="s", s=55, color=C_CORR,
                       edgecolor="white", zorder=5,
                       label="Algo 0.87×" if i == 0 else None)
        if r["occluded"]:
            ax.axvspan(i - 0.5, i + 0.5, color=C_RED, alpha=0.06, zorder=0)
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("Groove depth  (mm)")
    ax.grid(True, axis="y", color=GRID, linewidth=0.6)
    if own:
        ax.set_title("%s — LMI reading spread vs algo" % axle_title(cmp["axle"]),
                     fontsize=12, color=C_NAVY)
    _legend(ax)
    _tighten(fig, own)


def draw_overview(fig, cmp):
    """Two stacked panels for the embedded canvas: depth-vs-position over Δ bars."""
    if not MATPLOTLIB_AVAILABLE:
        return
    fig.clear()
    if not (cmp.get("grooves")):
        _no_data(fig, "No algo grooves for this axle.")
        return
    gs = fig.add_gridspec(2, 1, height_ratios=[1.35, 1.0], hspace=0.42)
    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[1, 0])
    draw_depth_vs_position(fig, cmp, ax=ax1)
    ax1.set_title("Depth vs position", fontsize=11, color=C_NAVY)
    draw_delta_bars(fig, cmp, ax=ax2)
    ax2.set_title("Algo − LMI per groove", fontsize=11, color=C_NAVY)
    fig.suptitle("%s" % axle_title(cmp["axle"]), fontsize=12.5, color=C_NAVY,
                 fontweight="bold")
    fig.text(0.5, 0.005, _subtitle(cmp), ha="center", fontsize=7.5, color=C_TEXT_DIM)
    # explicit margins (not tight_layout: errorbar+legend axes are not tight-compatible)
    try:
        fig.subplots_adjust(left=0.09, right=0.98, top=0.90, bottom=0.08, hspace=0.42)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def _scatter_line(ax, rows, xk, yk, color, label, marker):
    pts = [(r[xk], r[yk]) for r in rows if _finite(r[xk]) and _finite(r[yk])]
    if not pts:
        return
    pts.sort()
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    ax.plot(xs, ys, marker=marker, ms=6, color=color, label=label, linewidth=1.4,
            linestyle="-", alpha=0.9, zorder=4)


def _annotate_labels(ax, rows, xk, yk):
    for r in rows:
        if _finite(r[xk]) and _finite(r[yk]):
            ax.annotate(r["label"], (r[xk], r[yk]), textcoords="offset points",
                        xytext=(0, 8), ha="center", fontsize=7, color=C_TEXT_DIM)


def _shade_occluded(ax, rows, xk):
    occ = [r[xk] for r in rows if r["occluded"] and _finite(r[xk])]
    if occ:
        lo, hi = min(occ), max(occ)
        pad = max((hi - lo) * 0.08, 10)
        ax.axvspan(lo - pad, hi + pad, color=C_RED, alpha=0.05, zorder=0,
                   label="inner tyre (occluded)")


def _legend(ax):
    h, l = ax.get_legend_handles_labels()
    if h:
        leg = ax.legend(h, l, fontsize=7.5, framealpha=0.9, loc="best")
        try:
            leg.set_in_layout(True)
        except Exception:
            pass


def _tighten(fig, own):
    if own:
        try:
            fig.tight_layout(rect=(0, 0.03, 1, 1))
        except Exception:
            pass
