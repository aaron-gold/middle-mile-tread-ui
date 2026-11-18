"""Matplotlib drawing for the frame-yield view (PURE: takes a Figure, draws on it).

A horizontal "fuel-gauge" per wheel/axle: a grey track = assigned/captured frames, an
orange fill = frames that carried a groove, with the count (with/assigned) and yield %
(green >=70, amber 40-69, red <40) in aligned columns. draw_yield = one scan; draw_compare
= two scans side by side (the 8 Hz vs 4 Hz view). Guard on MATPLOTLIB_AVAILABLE first.
"""

from mm_viewer.common import MATPLOTLIB_AVAILABLE
from mm_viewer.theme import C_ORANGE, C_NAVY, C_TEXT, C_TEXT_DIM

C_TRACK = "#e3e9f2"
C_GRID = "#d6deea"
C_OK = "#2f9e44"
C_WARN = "#e8a21e"
C_BAD = "#d64545"
C_GAPBG = "#f7eaea"


def _yclr(p):
    return C_OK if p >= 70 else (C_WARN if p >= 40 else C_BAD)


def _no_data(fig, msg):
    fig.clear()
    ax = fig.add_subplot(111)
    ax.axis("off")
    ax.text(0.5, 0.5, msg, ha="center", va="center", wrap=True,
            fontsize=11, color=C_TEXT_DIM)


def _gauge(ax, rows, maxv):
    """Draw the horizontal gauges for `rows` onto `ax` (newest/first row at top)."""
    from matplotlib.patches import FancyBboxPatch, Rectangle
    from matplotlib.ticker import MaxNLocator
    ax.set_facecolor("#ffffff")
    n = len(rows)
    maxv = max(maxv, 1)
    ax.set_xlim(0, maxv * 1.40)
    ax.set_ylim(-0.6, n - 0.4)
    col_cnt = maxv * 1.05
    col_pct = maxv * 1.26
    for y, r in zip(range(n)[::-1], rows):
        ax.text(-maxv * 0.015, y, r["label"], ha="right", va="center",
                color=C_TEXT, fontsize=10)
        asg = r["assigned"]
        if asg is None:                       # data gap
            ax.add_patch(Rectangle((0, y - 0.30), maxv, 0.60, facecolor=C_GAPBG,
                                   edgecolor=C_BAD, lw=0.8, ls=":"))
            ax.text(maxv * 0.5, y, "no %s  (data gap)" % "scan_grooves_full.csv",
                    ha="center", va="center", color=C_BAD, fontsize=9, style="italic")
            continue
        wg = r["with_groove"] or 0
        ax.add_patch(FancyBboxPatch((0, y - 0.30), asg, 0.60,
                     boxstyle="round,pad=0,rounding_size=0.10",
                     facecolor=C_TRACK, edgecolor="none"))
        ax.add_patch(FancyBboxPatch((0, y - 0.30), max(wg, 0.001), 0.60,
                     boxstyle="round,pad=0,rounding_size=0.10",
                     facecolor=C_ORANGE, edgecolor="none"))
        p = (100.0 * wg / asg) if asg else 0
        ax.text(col_cnt, y, "%d/%d" % (wg, asg), ha="left", va="center",
                color=C_TEXT, fontsize=10, fontweight="bold")
        ax.text(col_pct, y, "%d%%" % round(p), ha="left", va="center",
                color=_yclr(p), fontsize=10, fontweight="bold")
    ax.tick_params(left=False, labelleft=False)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(C_GRID)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.tick_params(axis="x", colors=C_TEXT_DIM, labelsize=8)


def _legend(fig):
    fig.text(0.70, 0.95, "■ assigned", ha="left", va="center",
             color="#9fb0c4", fontsize=9)
    fig.text(0.815, 0.95, "■ with groove", ha="left", va="center",
             color=C_ORANGE, fontsize=9)


def draw_yield(fig, rows, title, subtitle, unit_word):
    fig.clear()
    fig.patch.set_facecolor("#ffffff")
    if not MATPLOTLIB_AVAILABLE:
        return
    if not rows:
        _no_data(fig, "No LMI groove data to show for this scan.")
        return
    fig.text(0.015, 0.95, title, ha="left", va="center",
             color=C_NAVY, fontsize=13, fontweight="bold")
    if subtitle:
        fig.text(0.015, 0.885, subtitle, ha="left", va="center",
                 color=C_TEXT_DIM, fontsize=8.5)
    _legend(fig)
    ax = fig.add_axes([0.12, 0.13, 0.74, 0.66])
    maxv = max([r["assigned"] for r in rows if r["assigned"]] or [1])
    _gauge(ax, rows, maxv)
    ax.set_xlabel("%s   ·   grey = assigned / captured, orange = with a groove "
                  "measurement" % unit_word, color=C_TEXT_DIM, fontsize=8.5)


def draw_compare(fig, left_rows, right_rows, left_title, right_title, unit_word):
    fig.clear()
    fig.patch.set_facecolor("#ffffff")
    if not MATPLOTLIB_AVAILABLE:
        return
    if not left_rows and not right_rows:
        _no_data(fig, "No LMI groove data to compare.")
        return
    fig.text(0.5, 0.955, "LMI frame yield — comparison", ha="center",
             va="center", color=C_NAVY, fontsize=13, fontweight="bold")
    _legend(fig)
    # a shared x-scale so the two runs are read against the same frame count
    maxv = max([r["assigned"] for r in (left_rows + right_rows) if r["assigned"]]
               or [1])
    for i, (rows, ttl) in enumerate(((left_rows, left_title),
                                     (right_rows, right_title))):
        fig.text(0.07 + i * 0.49, 0.89, ttl, ha="left", va="center",
                 color=C_NAVY, fontsize=10, fontweight="bold")
        ax = fig.add_axes([0.10 + i * 0.49, 0.13, 0.36, 0.70])
        if rows:
            _gauge(ax, rows, maxv)
        else:
            ax.axis("off")
            ax.text(0.5, 0.5, "no data", ha="center", va="center",
                    color=C_TEXT_DIM, fontsize=10)
        ax.set_xlabel(unit_word, color=C_TEXT_DIM, fontsize=8.5)
