"""Laser Grooves tab: vehicle map (left) + selected wheel (right), issues on top."""

import csv
import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from mm_viewer.common import lp_open
from mm_viewer.theme import C_RED, C_TEXT_DIM
from mm_viewer.widgets.layout import allow_shrink
from mm_viewer.laser_grooves.data import depth_limit, load_scan_grooves, profile_errors
from mm_viewer.laser_grooves.detail import C_AMBER, WheelDetail
from mm_viewer.laser_grooves.vehicle import TRASH, VehicleMap
from mm_viewer.laser_grooves.worker import ProfilesWorker

ISSUE_ICON = {"error": "●", "warn": "▲", "info": "•"}
ISSUE_COLOUR = {"error": C_RED, "warn": C_AMBER, "info": C_TEXT_DIM}


def fit_issue_list(lst, max_rows=3):
    """Height for up to max_rows rows (scroll beyond); hidden when empty."""
    n = lst.count()
    lst.setVisible(n > 0)
    if n:
        row = max(lst.sizeHintForRow(0), 18)
        lst.setFixedHeight(min(n, max_rows) * row + 2 * lst.frameWidth() + 4)


def summary_html(sg):
    ws = sg.wheel_list()
    measured = [w for w in ws if w.primary is not None and w.primary.grooves]
    ng = sum(len(w.primary.grooves) for w in measured)
    low = None
    for w in measured:
        g = w.primary.min_groove()
        if g is not None and (low is None or g.depth < low[1].depth):
            low = (w, g)
    bits = ["<b>%d axle%s</b>" % (len(sg.axles), "" if len(sg.axles) == 1 else "s"),
            "%d / %d wheels measured" % (len(measured), len(ws)),
            "%d grooves" % ng]
    nt = sum(len(m.assigned) for m in sg.trash.values())
    if low is not None:
        w, g = low
        col = C_RED if g.depth < depth_limit(w.axle) else "#10202e"
        bits.append("lowest <b style='color:%s'>%.1f mm</b> (%s, %s G%d)"
                    % (col, g.depth, w.name(), g.tire, g.index))
    if nt:
        bits.append("%d profile%s in Trash" % (nt, "" if nt == 1 else "s"))
    return "  ·  ".join(bits)


def export_rows(sg, profiles):
    """One row per groove of every wheel's primary measurement (+ a row for wheels
    with no groove, so a gap is never silent)."""
    rows = []
    for w in sg.wheel_list():
        m = w.primary
        mp = profiles.get((m.unit, m.axle)) if m is not None else None
        base = {"scan_id": sg.scan_id or "", "axle": w.axle, "side": w.side,
                "laser": m.unit if m else "", "profiles_assigned":
                    mp.n_assigned if mp else (len(m.assigned) if m else 0),
                "profiles_used_by_algo": mp.n_used if mp else ""}
        if m is None or not m.grooves:
            rows.append(dict(base, tyre="", groove="", note="; ".join(
                t for _l, t in w.errors) or "no grooves"))
            continue
        lim = depth_limit(w.axle)
        for g in m.grooves:
            rows.append(dict(base, tyre=g.tire, groove="G%d" % g.index,
                             edge_mm=_r(g.edge_mm, 1), depth_median_mm=_r(g.depth, 2),
                             depth_mean_mm=_r(g.depth_avg, 2), std_mm=_r(g.std, 2),
                             frames=g.n, bottom_found=g.bottom,
                             below_limit=(g.depth is not None and g.depth < lim),
                             note=""))
    return rows


EXPORT_COLS = ("scan_id", "axle", "side", "laser", "tyre", "groove", "edge_mm",
               "depth_median_mm", "depth_mean_mm", "std_mm", "frames", "bottom_found",
               "below_limit", "profiles_assigned", "profiles_used_by_algo", "note")


def _r(v, n):
    return "" if v is None else round(v, n)


class LaserGroovesTab(QWidget):
    def __init__(self, scan_folder, parent=None):
        super().__init__(parent)
        self.scan_folder = scan_folder
        self.sg = load_scan_grooves(scan_folder)
        self.profiles = {}
        self.extra = {}                         # wheel key -> errors found in its profiles
        self._worker = None

        self.summary = QLabel(summary_html(self.sg))
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        allow_shrink(self.summary, 200)
        self.progress = QLabel("")
        self.progress.setObjectName("hintText")
        self.export_btn = QPushButton("Export CSV…")
        self.export_btn.setToolTip("Every groove of every wheel, one row each")
        self.export_btn.clicked.connect(self.export_csv)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(self.summary, 1)
        top.addWidget(self.progress)
        top.addWidget(self.export_btn)

        self.issues = QListWidget()
        self.issues.setObjectName("issueList")
        self.issues.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.issues.itemClicked.connect(self._on_issue)
        self._fill_issues()

        self.map = VehicleMap()
        self.map.set_scan(self.sg)
        self.detail = WheelDetail()
        self.detail.profiles = self.profiles
        self.detail.extra = self.extra
        self.map.wheelSelected.connect(self._on_wheel)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.map)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 11)
        split.setStretchFactor(1, 9)
        split.setChildrenCollapsible(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)
        root.addLayout(top)
        root.addWidget(self.issues)
        root.addWidget(split, 1)

        if self.sg.wheels:
            first = sorted(self.sg.wheels)[0]
            self.map.select(first)
        self._start()

    # ---- issues ----
    def _fill_issues(self):
        self.issues.clear()
        items = list(self.sg.notes)
        keyed = []
        for w in self.sg.wheel_list():
            for lvl, txt in w.errors + self.extra.get(w.key, []):
                keyed.append((lvl, "%s: %s" % (w.name(), txt), w.key))
        for m in self.sg.trash_list():
            mp = self.profiles.get((m.unit, None))
            if mp is not None and mp.n_data:
                keyed.append(("info", "Trash · %s %s laser: %d valid profile%s of %d - "
                              "click to check" % (m.side or "?", m.laser or "?", mp.n_data,
                                                  "" if mp.n_data == 1 else "s",
                                                  mp.n_assigned), (TRASH, m.unit)))
        order = {"error": 0, "warn": 1, "info": 2}
        for lvl, txt, key in sorted([(l, t, None) for l, t in items] + keyed,
                                    key=lambda e: order.get(e[0], 3)):
            it = QListWidgetItem("%s  %s" % (ISSUE_ICON.get(lvl, "•"), txt))
            it.setForeground(QColor(ISSUE_COLOUR.get(lvl, C_TEXT_DIM)))
            it.setData(Qt.ItemDataRole.UserRole, key)
            self.issues.addItem(it)
        fit_issue_list(self.issues)

    def _on_issue(self, it):
        key = it.data(Qt.ItemDataRole.UserRole)
        if key is not None:
            self.map.select(tuple(key))

    def _on_wheel(self, key):
        if key[0] == TRASH:
            m = self.sg.trash.get(key[1])
            if m is not None:
                self.detail.show_trash(m, self.sg.unit_frames.get(m.unit, {}))
            return
        w = self.sg.wheels.get(key)
        if w is not None:
            self.detail.show_wheel(w)

    # ---- profiles ----
    def _start(self):
        ms = [w.primary for w in self.sg.wheel_list() if w.primary is not None]
        ms += [m for m in self.sg.measurements() if m not in ms and m.has_result]
        ms += self.sg.trash_list()
        ms = [m for m in ms if m.frames()]
        if not ms:
            return
        self._worker = ProfilesWorker(ms)
        self._worker.measReady.connect(self._on_profiles)
        self._worker.progress.connect(self._on_progress)
        self._worker.failed.connect(self._on_failed)
        self.progress.setText("Reading profiles…")
        self._worker.start()

    def _on_profiles(self, meas, mp):
        """Called twice per measurement: ungrouped (fast), then grouped."""
        self.profiles[(meas.unit, meas.axle)] = mp
        if meas.is_trash:
            if mp.grouped and mp.n_data:
                self._fill_issues()
        else:
            w = self.sg.wheels.get((meas.axle, meas.side))
            if w is not None and meas is w.primary and not mp.grouped:
                errs = profile_errors(mp)
                if errs:
                    self.extra[w.key] = errs
                    self._fill_issues()
        self.map.set_profiles(meas, mp)
        self.detail.profiles_ready(meas)

    def _on_progress(self, i, n):
        self.progress.setText("" if i >= n else "Reading profiles… %d / %d" % (i, n))

    def _on_failed(self, tb):
        sys.stderr.write(tb.rstrip() + os.linesep)
        self.progress.setText("Profile read failed - see the console")

    # ---- export ----
    def export_csv(self):
        sid = (self.sg.scan_id or "scan")[:8]
        path, _f = QFileDialog.getSaveFileName(
            self, "Export groove depths", os.path.join(self.scan_folder or "",
                                                       "%s_groove_depths.csv" % sid),
            "CSV (*.csv)")
        if not path:
            return
        try:
            with lp_open(path, "w", encoding="utf-8", newline="") as fh:
                wr = csv.DictWriter(fh, fieldnames=EXPORT_COLS, extrasaction="ignore")
                wr.writeheader()
                for r in export_rows(self.sg, self.profiles):
                    wr.writerow(r)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.progress.setText("Exported " + os.path.basename(path))

    # ---- lifecycle ----
    def shutdown(self):
        w, self._worker = self._worker, None
        if w is not None:
            for sig in (w.measReady, w.progress, w.failed):
                try:
                    sig.disconnect()
                except (TypeError, RuntimeError):
                    pass
            try:
                w.requestInterruption()
                if w.isRunning():
                    w.wait(5000)
            except RuntimeError:
                pass
        self.detail.shutdown()
