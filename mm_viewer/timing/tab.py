"""Timing tab: key numbers, timeline, gate dwell per axle, profile assignment vs gate edges."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from mm_viewer.common import Figure, FigureCanvas, MATPLOTLIB_AVAILABLE, NavigationToolbar
from mm_viewer.theme import C_NAVY, C_TEXT_DIM
from mm_viewer.timing.compute import build_timing, TOL_MS
from mm_viewer.timing.plot import axle_colour, draw_timeline
from mm_viewer.laser_grooves.tab import C_AMBER, fit_issue_list, ISSUE_COLOUR, ISSUE_ICON

# (header, tooltip)
PROF_COLS = (
    ("Axle", ""), ("Side", ""), ("Laser", "LMI unit that measured the wheel"),
    ("Gate", "the gate that opens this laser's window"),
    ("On gate [ms]", "how long the wheel held that gate (rise -> fall)"),
    ("Profiles", "profiles the wheel assembler assigned to this wheel"),
    ("First − rise [ms]", "first assigned profile minus the gate RISING edge"),
    ("Last − fall [ms]", "last assigned profile minus the gate FALLING edge = how long "
                         "after the fall profiles were still assigned"),
    ("Window [ms]", "acquisition window after the gate fall: front lasers 400 ms "
                    "after the gate3 fall, rear lasers 400 ms after the gate1 fall"),
    ("Laser pulses", "laser-enable pulses between the gate rise and the last profile"),
)


def _ms(v):
    return "—" if v is None else "%.0f" % v


def _table(cols):
    t = QTableWidget(0, len(cols))
    t.setObjectName("depthTable")
    for i, (head, tip) in enumerate(cols):
        it = QTableWidgetItem(head)
        if tip:
            it.setToolTip(tip)
        t.setHorizontalHeaderItem(i, it)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    t.horizontalHeader().setMinimumSectionSize(36)
    t.setMinimumHeight(110)
    return t


def _item(text, colour=None, bold=False, tip="", italic=False):
    it = QTableWidgetItem(text)
    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    if colour:
        it.setForeground(QColor(colour))
    if bold or italic:
        f = QFont(it.font())
        f.setBold(bold)
        f.setItalic(italic)
        it.setFont(f)
    if tip:
        it.setToolTip(tip)
    return it


def key_numbers(tm):
    """[(label, value, tooltip, warn)] for the strip on top."""
    rows = [r for r in tm.rows if r.primary]
    tails = [r for r in rows if r.tail_ms is not None]
    worst = max(tails, key=lambda r: r.tail_ms) if tails else None
    out = [("Scan duration", "—" if tm.duration is None else "%.2f s" % tm.duration,
            "scan_entry → scan_exit", tm.duration is None),
           ("Axles", str(len(tm.passages)) if tm.passages else "—",
            "axles whose wheels were followed through the gates", False),
           ("Profiles assigned", str(sum(len(r.times) for r in rows)),
            "laser profiles the wheel assembler gave the measured wheels", False)]
    if worst is not None:
        out.append(("Latest after a gate fall", "%.0f ms" % worst.tail_ms,
                    "Axle %d %s, %s fall (window +%.0f ms)"
                    % (worst.axle, worst.side, worst.gate, worst.srs_tail_ms),
                    worst.tail_ms > worst.srs_tail_ms + TOL_MS))
    return out


class TimingTab(QWidget):
    def __init__(self, triggers_csv, sg=None, parent=None):
        super().__init__(parent)
        self.tm = tm = build_timing(triggers_csv, sg)

        # ---- key numbers: one compact strip ----
        strip = QFrame()
        strip.setObjectName("card")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(14, 6, 14, 6)
        sl.setSpacing(28)
        for label, value, tip, warn in key_numbers(tm):
            lab = QLabel("<span style='color:%s'>%s</span>&nbsp;&nbsp;"
                         "<b style='font-size:18px; color:%s'>%s</b>"
                         % (C_TEXT_DIM, label, C_AMBER if warn else C_NAVY, value))
            lab.setTextFormat(Qt.TextFormat.RichText)
            lab.setToolTip(tip)
            sl.addWidget(lab)
        sl.addStretch(1)

        self.issues = QListWidget()
        self._fill_issues()

        # ---- timeline ----
        self.show_all = QCheckBox("All controller outputs")
        self.show_all.setToolTip("Also draw the camera / flash triggers and traffic lights")
        self.show_all.toggled.connect(lambda _on: self._draw(self._xlim))
        self.reset_btn = QPushButton("Whole scan")
        self.reset_btn.clicked.connect(lambda: self._draw(None))
        hint = QLabel("Colour = axle  ·  grey = not matched to an axle / Trash  ·  "
                      "thin bar = laser acquisition window (gate fall + 400 ms)  ·  "
                      "click a table row to zoom")
        hint.setObjectName("hintText")
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        tl = QWidget()
        tv = QVBoxLayout(tl)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(2)
        self._xlim = None
        if MATPLOTLIB_AVAILABLE:
            self.fig = Figure(figsize=(10, 4), dpi=100)
            self.fig.patch.set_facecolor("white")
            self.canvas = FigureCanvas(self.fig)
            self.canvas.setMinimumHeight(220)
            self.toolbar = NavigationToolbar(self.canvas, self)
            self.toolbar.setIconSize(self.toolbar.iconSize() * 0.8)
            bar.addWidget(self.toolbar)
        else:                                       # pragma: no cover
            self.fig = self.canvas = self.toolbar = None
        bar.addWidget(hint, 1)
        bar.addWidget(self.show_all)
        bar.addWidget(self.reset_btn)
        tv.addLayout(bar)
        tv.addWidget(self.canvas if self.canvas is not None else
                     QLabel("matplotlib is not installed - no timeline"), 1)

        # ---- tables ----
        g = tm.gates
        dcols = [("Axle", "")] + [(x.replace("gate", "Gate "), "time the wheel held %s "
                                   "[ms] (rise -> fall)" % x) for x in g]
        if len(g) > 1:
            dcols.append(("%s→%s [s]" % (g[0].replace("gate", "G"),
                                           g[-1].replace("gate", "G")),
                          "from the first gate's rise to the last gate's rise"))
        self.dwell = _table(dcols)
        self._fill_dwell()
        self.prof = _table(PROF_COLS)
        self._fill_prof()
        self.dwell.itemSelectionChanged.connect(lambda: self._zoom_from(self.dwell))
        self.prof.itemSelectionChanged.connect(lambda: self._zoom_from(self.prof))
        tables = QSplitter(Qt.Orientation.Horizontal)
        tables.addWidget(self._titled("Time on each gate [ms]", self.dwell))
        tables.addWidget(self._titled("Profiles assigned vs the laser gate edges",
                                      self.prof))
        tables.setStretchFactor(0, 2)
        tables.setStretchFactor(1, 5)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(tl)
        split.addWidget(tables)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setChildrenCollapsible(False)
        split.setSizes([520, 300])

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(6)
        root.addWidget(strip)
        root.addWidget(self.issues)
        root.addWidget(split, 1)
        self._draw(None)

    @staticmethod
    def _titled(title, w):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        cap = QLabel(title.upper())
        cap.setObjectName("caption")
        v.addWidget(cap)
        v.addWidget(w, 1)
        return box

    # ---- fill ----
    def _fill_dwell(self):
        tm = self.tm
        if not tm.passages:
            self._fill_dwell_in_order()
            return
        axles = sorted(tm.passages)
        self.dwell.setRowCount(len(axles))
        for r, ax in enumerate(axles):
            chain = tm.passages[ax]
            first = _item("A%d" % ax, axle_colour(ax), bold=True)
            first.setData(Qt.ItemDataRole.UserRole, ax)
            self.dwell.setItem(r, 0, first)
            for c, g in enumerate(tm.gates, 1):
                p = chain.get(g)
                tip = "" if p is None else "rise %.3f s · fall %s" % (
                    p.rise, "—" if p.fall is None else "%.3f s" % p.fall)
                self.dwell.setItem(r, c, _item(_ms(p.dwell_ms) if p else "—", tip=tip))
            if len(tm.gates) > 1:
                a, b = chain.get(tm.gates[0]), chain.get(tm.gates[-1])
                v = "—" if a is None or b is None else "%.2f" % (b.rise - a.rise)
                self.dwell.setItem(r, len(tm.gates) + 1, _item(v))

    def _fill_dwell_in_order(self):
        """No axle passages (no manifest): the n-th pulse of every gate, side by side."""
        tm = self.tm
        n = max([len(tm.pulses.get(g, [])) for g in tm.gates] + [0])
        self.dwell.setRowCount(n)
        for r in range(n):
            self.dwell.setItem(r, 0, _item("#%d" % (r + 1), C_TEXT_DIM,
                                           tip="pulse number - axle unknown"))
            for c, g in enumerate(tm.gates, 1):
                ps = tm.pulses.get(g, [])
                p = ps[r] if r < len(ps) else None
                self.dwell.setItem(r, c, _item(_ms(p.dwell_ms) if p else "—"))
            if len(tm.gates) > 1:
                self.dwell.setItem(r, len(tm.gates) + 1, _item("—"))

    def _fill_prof(self):
        rows = [r for r in self.tm.rows if r.times]
        self.prof.setRowCount(len(rows))
        for i, r in enumerate(rows):
            dim = None if r.primary else C_TEXT_DIM
            tail_bad = r.tail_ms is not None and r.tail_ms > r.srs_tail_ms + TOL_MS
            lead_bad = r.lead_ms is not None and r.lead_ms < -TOL_MS
            first = _item("A%d" % r.axle, axle_colour(r.axle), bold=True)
            first.setData(Qt.ItemDataRole.UserRole, r.axle)
            vals = [
                first,
                _item(r.side or "—", dim),
                _item((r.laser or "?").capitalize() + ("" if r.primary else " (2nd)"), dim,
                      italic=not r.primary, tip=r.unit),
                _item(r.gate or "—", dim),
                _item(_ms(r.pulse.dwell_ms) if r.pulse else "—", dim),
                _item(str(len(r.times)), dim),
                _item(_ms(r.lead_ms), C_AMBER if lead_bad else dim, bold=lead_bad),
                _item(_ms(r.tail_ms), C_AMBER if tail_bad else dim, bold=tail_bad),
                _item("+%.0f" % r.srs_tail_ms, C_TEXT_DIM),
                _item("—" if r.n_laser is None else str(r.n_laser), dim),
            ]
            for c, it in enumerate(vals):
                self.prof.setItem(i, c, it)

    def _fill_issues(self):
        self.issues.clear()
        order = {"error": 0, "warn": 1, "info": 2}
        for lvl, txt in sorted(self.tm.notes, key=lambda n: order.get(n[0], 3)):
            it = QListWidgetItem("%s  %s" % (ISSUE_ICON.get(lvl, "•"), txt))
            it.setForeground(QColor(ISSUE_COLOUR.get(lvl, C_TEXT_DIM)))
            self.issues.addItem(it)
        fit_issue_list(self.issues)

    # ---- plot ----
    def _draw(self, xlim):
        self._xlim = xlim
        if self.fig is None:
            return
        draw_timeline(self.fig, self.tm, self.show_all.isChecked(), xlim)
        self.canvas.draw_idle()
        self.toolbar.update()                       # new home view for the toolbar

    def _zoom_from(self, table):
        rows = table.selectionModel().selectedRows()
        if not rows:
            return
        it = table.item(rows[0].row(), 0)
        ax = it.data(Qt.ItemDataRole.UserRole) if it else None
        win = self.tm.axle_window(ax) if ax is not None else None
        if win:
            pad = max(0.3, (win[1] - win[0]) * 0.15)
            self._draw((win[0] - pad, win[1] + pad))

    def shutdown(self):
        if self.fig is not None:
            self.fig.clear()
