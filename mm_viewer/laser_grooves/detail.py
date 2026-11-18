"""One wheel: its profiles, the algo groove depths, and what is wrong with it.

draw_wheel() is pure matplotlib (a Figure in, nothing else touched) so it can be tested
and exported without a window.
"""

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QSizePolicy,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from mm_viewer.common import Figure, FigureCanvas, MATPLOTLIB_AVAILABLE
from mm_viewer.theme import C_NAVY, C_RED, C_TEXT, C_TEXT_DIM
from mm_viewer.widgets.controls import ArrowComboBox, SegmentedControl
from mm_viewer.widgets.layout import allow_shrink
from mm_viewer.laser_grooves.data import depth_limit, groove_state, MIN_VALID_POINTS
from mm_viewer.laser_grooves.profiles import decimate, segments
from mm_viewer.laser_grooves.vehicle import STATE_COLOUR, STATE_TEXT

C_AMBER = "#c77800"
GROUP_COLOURS = ("#1e5fa8", "#e8791e", "#2e9e5b", "#8e44ad", "#c0392b", "#16a2b8")
TIRE_COLOURS = {"outer": "#e8791e", "inner": "#1e5fa8"}
VIEW_GROUPED, VIEW_RAW = "grouped", "raw"
MAX_ROWS = 3             # cuts drawn in the grouped view; the rest are counted


def _limit_label(axle):
    return '%s limit %.1f mm' % ('4/32"' if axle == 1 else '2/32"', depth_limit(axle))


# ----------------------------------------------------------------------------
# figure
# ----------------------------------------------------------------------------
def draw_wheel(fig, wheel, meas, mp, view=VIEW_GROUPED):
    """Profiles on top (one row per cut when grouped), groove depths below (per tyre)."""
    fig.clear()
    tires = meas.tires() if meas is not None else []
    ncol = max(1, len(tires))
    gs = fig.add_gridspec(2, ncol, height_ratios=(1.25, 1.0) if tires else (4.0, 0.5),
                          hspace=0.55, wspace=0.10,
                          left=0.095, right=0.985, top=0.93, bottom=0.13)
    _draw_profiles(fig, gs[0, :], mp, view)
    if not tires:
        ax = fig.add_subplot(gs[1, :])
        ax.set_axis_off()
        msg = ("No groove-depth result for this wheel" if meas is None or not
               meas.has_result else "The algo found no grooves on this wheel")
        ax.text(0.5, 0.5, msg, ha="center", va="center", color=C_RED, fontsize=11,
                transform=ax.transAxes)
        return
    lim = depth_limit(wheel.axle)
    dmax = max([(g.depth or 0) + (g.std or 0) for _t, gl in tires for g in gl] + [lim])
    share = None
    for i, (tire, gl) in enumerate(tires):
        ax = fig.add_subplot(gs[1, i], sharey=share)
        share = share or ax
        _draw_depths(ax, tire, gl, lim, dmax, wheel.axle, first=(i == 0))


def draw_trash(fig, meas, mp, view=VIEW_RAW):
    """Trash: the profiles only (no grooves were measured on them)."""
    fig.clear()
    gs = fig.add_gridspec(1, 1, left=0.095, right=0.985, top=0.93, bottom=0.10)
    _draw_profiles(fig, gs[0, 0], mp, view)


def _style(ax):
    ax.tick_params(labelsize=8, colors=C_TEXT_DIM)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def _message(fig, cell, text, colour):
    ax = fig.add_subplot(cell)
    ax.set_axis_off()
    ax.text(0.5, 0.5, text, ha="center", va="center", color=colour,
            transform=ax.transAxes)


def _draw_profiles(fig, cell, mp, view):
    if mp is None:
        _message(fig, cell, "Loading profiles…", C_TEXT_DIM)
        return
    good = [f for f in mp.frames if f.has_data]
    if not good:
        _message(fig, cell, "No usable profile among the %d assigned" % mp.n_assigned,
                 C_RED)
        return
    if view == VIEW_RAW or not mp.groups:
        ax = fig.add_subplot(cell)
        _style(ax)
        _draw_raw(ax, good)
        ax.margins(x=0.02, y=0.06)
        ax.autoscale_view()
        ax.set_xlabel("sensor x [mm]", fontsize=8, color=C_TEXT_DIM)
    else:
        _draw_grouped(fig, cell, mp)


def _lines(ax, frames_xy, colour, step=1.0, band=None):
    """
    frames_xy = [(FrameProfile, x, y)] -> one LineCollection for the frames the algo
    used and one for the rest. `band` = (lo, hi): points outside are left out (the line
    breaks there) instead of being squashed onto the edge.
    """
    from matplotlib.collections import LineCollection
    used, other = [], []
    for f, x, y in frames_xy:
        x, y = decimate(x, y, step) if step else (x, y)
        if band is not None:
            ok = (y >= band[0]) & (y <= band[1])
            cut = np.flatnonzero(np.diff(ok.astype(np.int8))) + 1
            runs = [(a, b) for a, b in zip(np.r_[0, cut], np.r_[cut, x.size])
                    if ok[a]]
        else:
            runs = [(0, x.size)]
        segs = [np.column_stack(sg) for a, b in runs
                for sg in segments(x[a:b], y[a:b])]
        (used if f.used else other).extend(segs)
    if used:
        ax.add_collection(LineCollection(used, colors=colour, linewidths=1.0))
    if other:
        ax.add_collection(LineCollection(other, colors=colour, linewidths=0.7,
                                         linestyles=(0, (3, 2)), alpha=0.55))


def _draw_raw(ax, good):
    n = len(good)
    for i, f in enumerate(good):
        _lines(ax, [(f, f.x, f.z)], _blend("#9cc3ea", "#0f2a3f", i / max(1, n - 1)))
    ax.autoscale_view()
    ax.set_ylabel("z [mm]", fontsize=8, color=C_TEXT_DIM)
    ax.set_title("Assigned profiles as recorded  ·  light → dark = earlier → later  ·  "
                 "dashed = not used by the algo", fontsize=9, color=C_TEXT, loc="left")


def _draw_grouped(fig, cell, mp):
    """
    ONE axis, one band per cut of the tyre (first frames on top), its frames overlaid.
    x is shared in mm; each band is scaled to its own height so a tall cut never
    flattens the others (the bands' vertical scales therefore differ).
    """
    ax = fig.add_subplot(cell)
    _style(ax)
    # only the cuts the algo measured (else the largest); the rest live in Raw
    order = sorted(mp.groups, key=lambda g: (not any(f.used for f in g), -len(g),
                                             g[0].frame))
    # (no frame used by the algo - Trash - : the largest cuts)
    shown = [g for g in order if any(f.used for f in g)][:MAX_ROWS] or order[:MAX_ROWS]
    hidden = [g for g in order if not any(g is h for h in shown)]
    n = len(shown)
    ticks, labels = [], []
    for gi, g in enumerate(shown):
        xz = [(f,) + tuple(f.fitted_xz()) for f in g]
        allz = np.concatenate([z for _f, _x, z in xz])
        zmin, zmax = np.percentile(allz, (2, 98))      # specks never set the scale
        s = 0.82 / max(float(zmax - zmin), 1e-6)
        base = (n - 1 - gi) * 1.0
        c = GROUP_COLOURS[gi % len(GROUP_COLOURS)]
        _lines(ax, [(f, x, base + 0.06 + (z - zmin) * s) for f, x, z in xz], c,
               band=(base, base + 0.97))
        fr = [f.frame for f in g]
        lab = "f%d" % fr[0] if len(fr) == 1 else "f%d–%d" % (fr[0], fr[-1])
        ticks.append(base + 0.47)
        labels.append(("%s · %d" % (lab, len(fr)), c if any(f.used for f in g)
                       else C_TEXT_DIM))
        if gi:
            ax.axhline(base + 1.0, color="#eef2f8", lw=0.8)
    k = sum(len(g) for g in hidden)
    more = ("   ·   %d other frame%s not drawn (Raw shows every frame)"
            % (k, "" if k == 1 else "s")) if k else ""
    ax.autoscale_view(scaley=False)
    ax.set_ylim(-0.04, n + 0.02)
    ax.set_yticks(ticks)
    ax.set_yticklabels([t for t, _c in labels], fontsize=8)
    for tl, (_t, c) in zip(ax.get_yticklabels(), labels):
        tl.set_color(c)
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    ax.margins(x=0.02)
    ax.set_xlabel("sensor x [mm]" + ("   ·   each row scaled to its own height"
                                     if n > 1 else "") + more, fontsize=8,
                  color=C_TEXT_DIM)
    what = ("Profiles the algo measured" if any(f.used for g in shown for f in g)
            else "Valid profiles")
    title = (what + "  ·  one row per cut of the tyre, overlaid" if n > 1
             else what + "  ·  overlaid (rigid fit)")
    ax.set_title(title + "  ·  dashed = not used by the algo", fontsize=9, color=C_TEXT,
                 loc="left")


def _draw_depths(ax, tire, grooves, lim, dmax, axle, first):
    """One stem per groove, coloured like its stripe on the vehicle map; the value is
    written under the stem's tip and G<n> above it, so number and groove read as one."""
    c = TIRE_COLOURS.get(tire, C_NAVY)
    has_edge = all(g.edge_mm is not None for g in grooves)
    xs = [g.edge_mm if has_edge else g.index for g in grooves]
    span = (max(xs) - min(xs)) if len(xs) > 1 else (40.0 if has_edge else 1.0)
    pad = max(span * 0.12, 12.0 if has_edge else 0.6)
    ax.axhspan(0, dmax * 0.2, color="#2b3440", alpha=0.08, lw=0)   # the tread surface
    ax.axhline(0, color="#9aa8bd", lw=1.0)
    ax.axhline(-lim, color=C_RED, lw=1.0, ls=(0, (4, 3)))
    for x, g in zip(xs, grooves):
        d = g.depth
        if d is None:
            continue
        sc = STATE_COLOUR[groove_state(d, axle)]
        ax.plot([x, x], [0, -d], color=sc, lw=4.0, solid_capstyle="butt")
        if g.std:
            ax.errorbar([x], [-d], yerr=[[g.std], [g.std]], fmt="none", ecolor=C_TEXT_DIM,
                        elinewidth=0.9, capsize=3)
        ax.plot([x], [-d], "o", ms=7, mec=sc, mew=1.6,
                mfc=sc if g.bottom else "white")
        ax.text(x, -d - max(g.std or 0, 0) - dmax * 0.05, "%.1f" % d, ha="center",
                va="top", fontsize=9.5, fontweight="bold",
                color=STATE_TEXT[groove_state(d, axle)],
                bbox=dict(boxstyle="round,pad=0.25", fc=sc, ec="none"))
        ax.text(x, dmax * 0.06, "G%d" % g.index, ha="center", va="bottom", fontsize=8.5,
                color=c, fontweight="bold")
    ax.set_xlim(min(xs) - pad, max(xs) + pad)
    ax.set_ylim(-dmax * 1.32, dmax * 0.2)
    ax.set_title({"outer": "Outer tyre", "inner": "Inner tyre"}.get(tire, "Tyre"),
                 fontsize=9, color=c, loc="left", fontweight="bold")
    ax.set_xlabel("distance from wheel edge [mm]  (outside → inside)" if has_edge
                  else "groove (no edge position in the result)", fontsize=8,
                  color=C_TEXT_DIM)
    ax.tick_params(labelsize=8, colors=C_TEXT_DIM)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.text(1.0, 1.04, "- - " + _limit_label(axle), transform=ax.transAxes,
            fontsize=7.5, color=C_RED, ha="right", va="bottom")
    if first:
        ax.set_ylabel("depth [mm]", fontsize=8, color=C_TEXT_DIM)
        ax.yaxis.set_major_formatter(_abs_formatter())
    else:
        ax.tick_params(labelleft=False)


def _abs_formatter():
    from matplotlib.ticker import FuncFormatter
    return FuncFormatter(lambda v, _p: "%g" % abs(v))


def _blend(a, b, t):
    ca, cb = QColor(a), QColor(b)
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * t)) for x, y in (
        (ca.red(), cb.red()), (ca.green(), cb.green()), (ca.blue(), cb.blue())))


# ----------------------------------------------------------------------------
# widget
# ----------------------------------------------------------------------------
class WheelDetail(QFrame):
    """Header, profile-quality line, errors, figure, groove table."""

    COLS = ("Tyre", "Groove", "From edge [mm]", "Depth [mm]", "± [mm]", "Frames",
            "Bottom found")
    TRASH_COLS = ("Frame", "From vehicle enter [s]", "Valid points", "Valid profile")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.wheel = None
        self.trash = None                       # a Trash Measurement when one is shown
        self.trash_frames = {}
        self.meas = None
        self._cols = self.COLS
        self.profiles = {}                      # shared dicts owned by the tab
        self.extra = {}
        self.view = VIEW_GROUPED

        self.title = QLabel("Select a wheel")
        self.title.setObjectName("detailTitle")
        self.title.setStyleSheet("font-size:17px; font-weight:700; color:%s;" % C_NAVY)
        allow_shrink(self.title, 120)
        self.laser_combo = ArrowComboBox()
        self.laser_combo.setToolTip("This wheel was measured by more than one laser")
        self.laser_combo.setVisible(False)
        self.laser_combo.currentIndexChanged.connect(self._on_laser)
        self.view_seg = SegmentedControl([(VIEW_GROUPED, "Grouped"), (VIEW_RAW, "Raw")])
        self.view_seg.set_current(VIEW_GROUPED)
        self.view_seg.setToolTip("Grouped: frames that fit each other overlaid.\n"
                                 "Raw: every profile as the sensor recorded it.")
        self.view_seg.changed.connect(self._on_view)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(self.title, 1)
        head.addWidget(self.laser_combo)
        head.addWidget(self.view_seg)

        self.quality = QLabel("")
        self.quality.setObjectName("hintText")
        self.quality.setWordWrap(True)
        self.errors = QLabel("")
        self.errors.setWordWrap(True)
        self.errors.setTextFormat(Qt.TextFormat.RichText)
        self.errors.setVisible(False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        lay.addLayout(head)
        lay.addWidget(self.quality)
        lay.addWidget(self.errors)
        if MATPLOTLIB_AVAILABLE:
            self.fig = Figure(figsize=(7, 5), dpi=100)
            self.fig.patch.set_facecolor("white")
            self.canvas = FigureCanvas(self.fig)
            self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Expanding)
            self.canvas.setMinimumHeight(260)
            plot = self.canvas
        else:                                   # pragma: no cover
            self.fig = self.canvas = None
            plot = QLabel("matplotlib is not installed - no plots")

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setObjectName("depthTable")
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setMinimumHeight(70)
        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.addWidget(plot)
        self.split.addWidget(self.table)
        self.split.setStretchFactor(0, 4)
        self.split.setStretchFactor(1, 1)
        self.split.setChildrenCollapsible(False)
        self.split.setSizes([620, 170])
        lay.addWidget(self.split, 1)

    # ---- data ----
    def show_wheel(self, wheel):
        self.wheel, self.trash = wheel, None
        ms = sorted(wheel.meas, key=lambda m: m is not wheel.primary)
        ms = [m for m in ms if m.has_result] or ms
        self.laser_combo.blockSignals(True)
        self.laser_combo.clear()
        for m in ms:
            self.laser_combo.addItem(m.label(), m)
        self.laser_combo.setVisible(len(ms) > 1)
        self.laser_combo.blockSignals(False)
        self.meas = ms[0] if ms else None
        self.refresh()

    def show_trash(self, meas, unit_frames=None):
        """Trash of one laser: its profiles + one table row per frame."""
        self.wheel, self.trash, self.meas = None, meas, meas
        self.trash_frames = unit_frames or {}
        self.laser_combo.setVisible(False)
        self.refresh()

    def profiles_ready(self, meas):
        if meas is self.meas:
            self.refresh(table=self.trash is not None)

    def _on_laser(self, i):
        m = self.laser_combo.itemData(i)
        if m is not None:
            self.meas = m
            self.refresh()

    def _on_view(self, key):
        self.view = key
        self.refresh(table=False)

    def _set_errors(self, errs):
        if errs:
            self.errors.setText("<br>".join(
                "<span style='color:%s'>%s %s</span>" % (
                    C_RED if lvl == "error" else C_AMBER if lvl == "warn" else C_TEXT_DIM,
                    "●" if lvl == "error" else "▲" if lvl == "warn" else "•", txt)
                for lvl, txt in errs))
        self.errors.setVisible(bool(errs))

    def refresh(self, table=True):
        if self.trash is not None:
            self._refresh_trash(table)
            return
        w, m = self.wheel, self.meas
        if w is None:
            return
        sub = (" — " + m.label()) if m is not None else ""
        self.title.setText("Axle %d · %s%s" % (w.axle, w.side, sub))
        mp = self.profiles.get((m.unit, m.axle)) if m is not None else None
        self.quality.setText(self._quality_text(m, mp))
        errs = (list(w.errors) + self.extra.get(w.key, [])) if m is w.primary else []
        if m is not None and m is not w.primary:
            errs.append(("info", "secondary measurement - the vehicle map shows the %s"
                         % (w.primary.label() if w.primary else "primary")))
        self._set_errors(errs)
        if self.fig is not None:
            draw_wheel(self.fig, w, m, mp, self.view)
            self.canvas.draw_idle()
        if table:
            self._fill_table(w, m)

    def _refresh_trash(self, table):
        m = self.trash
        self.title.setText("Trash · %s — %s" % (m.side or "?", m.label()))
        mp = self.profiles.get((m.unit, None))
        n = len(m.assigned)
        head = "%d profile%s the wheel assembler gave no axle" % (n, "" if n == 1 else "s")
        if mp is None:
            q = head + " · checking…"
        else:
            fr = sorted(m.assigned)
            miss = ("  ·  %d file%s missing" % (len(mp.missing), "" if len(mp.missing) == 1
                                                else "s")) if mp.missing else ""
            q = "%s · %d valid (≥ %d points after cleaning)%s  ·  frames f%d–f%d" % (
                head, mp.n_data, MIN_VALID_POINTS, miss, fr[0], fr[-1])
        self.quality.setText(q)
        errs = []
        if mp is not None and mp.n_data:
            errs.append(("warn", "%d valid profile%s in Trash - check whether they belong "
                                 "to a wheel" % (mp.n_data, "" if mp.n_data == 1 else "s")))
        self._set_errors(errs)
        if self.fig is not None:
            draw_trash(self.fig, m, mp, self.view)
            self.canvas.draw_idle()
        if table:
            self._fill_trash_table(m, mp)

    @staticmethod
    def _quality_text(m, mp):
        if m is None:
            return "No laser measured this wheel"
        if mp is None:
            return "Profiles: %d assigned · loading…" % len(m.frames())
        bits = ["%d assigned" % mp.n_assigned, "%d with data" % mp.n_data,
                "%d used by the algo" % mp.n_used]
        if mp.groups and len(mp.groups) > 1:
            bits.append("%d cuts of the tyre" % len(mp.groups))
        if mp.missing:
            bits.append("%d file%s missing" % (len(mp.missing),
                                               "" if len(mp.missing) == 1 else "s"))
        frames = m.frames()
        span = ("  ·  frames f%d–f%d" % (frames[0], frames[-1])) if frames else ""
        return "Profiles: " + " · ".join(bits) + span

    def _set_cols(self, cols):
        if tuple(self._cols) != tuple(cols):
            self._cols = tuple(cols)
            self.table.setColumnCount(len(cols))
            self.table.setHorizontalHeaderLabels(cols)

    def _fill_table(self, w, m):
        self._set_cols(self.COLS)
        rows = m.grooves if m is not None else []
        self.table.setRowCount(len(rows))
        for r, g in enumerate(rows):
            vals = (g.tire.capitalize(), "G%d" % g.index,
                    "—" if g.edge_mm is None else "%.0f" % g.edge_mm,
                    "—" if g.depth is None else "%.1f" % g.depth,
                    "—" if g.std is None else "%.2f" % g.std,
                    str(g.n), "yes" if g.bottom else "no")
            st = groove_state(g.depth, w.axle)
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if c == 1:
                    it.setForeground(QColor(TIRE_COLOURS.get(g.tire, C_NAVY)))
                if c == 3 and g.depth is not None:
                    # the same chip colour as on the vehicle map
                    it.setBackground(QColor(STATE_COLOUR[st]))
                    it.setForeground(QColor(STATE_TEXT[st]))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    if g.depth_orig is not None:
                        it.setToolTip("median depth %.2f mm · mean %.2f mm · before the "
                                      "algo scale %.2f mm" % (
                                          g.depth, g.depth_avg or float("nan"),
                                          g.depth_orig))
                self.table.setItem(r, c, it)
        self.table.setVisible(bool(rows))

    def _fill_trash_table(self, m, mp):
        self._set_cols(self.TRASH_COLS)
        by = {f.frame: f for f in mp.frames} if mp is not None else {}
        frames = sorted(m.assigned)
        self.table.setRowCount(len(frames))
        for r, n in enumerate(frames):
            f = by.get(n)
            rec = self.trash_frames.get(n)
            t = rec[1] if rec else None
            if mp is None:
                pts, ok = "…", "…"
            elif f is None:
                pts, ok = "—", "missing"
            else:
                pts, ok = str(f.n_valid), "yes" if f.has_data else "no"
            vals = ("f%d" % n, "—" if t is None else "%.3f" % t, pts, ok)
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if c == 3 and ok == "yes":
                    it.setForeground(QColor("#b5651d"))
                    fb = it.font()
                    fb.setBold(True)
                    it.setFont(fb)
                self.table.setItem(r, c, it)
        self.table.setVisible(bool(frames))

    def shutdown(self):
        if self.fig is not None:
            self.fig.clear()

