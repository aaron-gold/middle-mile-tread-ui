"""The Frame-yield panel: Group (per unit / per axle) + Axle selectors, an embedded
gauge chart, and an in-tool 'Compare scan...' that puts another scan beside this one
(the 8 Hz vs 4 Hz view). Hosted by both the Laser Grooves (LMI) tab and the Diagnostics
tab; each passes the folders it has and the granularity it opens on.

Compute is synchronous (a few small CSV reads), so there is no worker here; shutdown()
only clears the figure. The canvas redraws on every control change via draw_idle().
"""

import os
import traceback

from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
)

from mm_viewer.common import Figure, FigureCanvas, MATPLOTLIB_AVAILABLE
from mm_viewer.widgets.controls import ArrowComboBox
from mm_viewer.widgets.layout import allow_shrink, field_label
from mm_viewer.frame_yield import compute, plot


GROUP_UNIT = "Per LMI unit"
GROUP_AXLE = "Per axle × wheel"


class FrameYieldPanel(QWidget):
    def __init__(self, groove_folder=None, diag_folder=None, axle_index=None,
                 default_group="unit", scan_folder=None, parent=None):
        super().__init__(parent)
        self.groove_folder = groove_folder
        self.diag_folder = diag_folder
        self.scan_folder = scan_folder or compute.scan_root_of(groove_folder or diag_folder)
        self._compare_root = None
        self._closing = False

        self._has_unit = bool(groove_folder)
        self._has_axle = bool(diag_folder)

        # ---- controls ----
        self.group_combo = ArrowComboBox()
        if self._has_axle:
            self.group_combo.addItem(GROUP_AXLE)
        if self._has_unit:
            self.group_combo.addItem(GROUP_UNIT)
        self.group_combo.setMinimumWidth(150)
        want = GROUP_AXLE if (default_group == "axle" and self._has_axle) else GROUP_UNIT
        i = self.group_combo.findText(want)
        self.group_combo.setCurrentIndex(i if i >= 0 else 0)

        self.axle_combo = ArrowComboBox()
        self.axle_combo.addItem("All axles")
        for n in compute.axle_numbers(diag_folder):
            self.axle_combo.addItem("Axle %s" % n, n)
        self.axle_combo.setMinimumWidth(120)

        self.compare_btn = QPushButton("Compare scan…")
        self.compare_btn.setObjectName("ghostBtn")
        self.compare_btn.setToolTip(
            "Pick another scan folder and show its frame yield beside this one "
            "(e.g. an 8 Hz run against a 4 Hz run).")
        self.clear_btn = QPushButton("✕ clear compare")
        self.clear_btn.setObjectName("ghostBtn")
        self.clear_btn.setVisible(False)

        self.note = QLabel("")
        self.note.setObjectName("hintText")
        allow_shrink(self.note, 160)

        top = QHBoxLayout()
        top.setContentsMargins(20, 10, 20, 2)
        top.setSpacing(10)
        top.addWidget(field_label("Group"))
        top.addWidget(self.group_combo)
        top.addSpacing(6)
        top.addWidget(field_label("Axle"))
        top.addWidget(self.axle_combo)
        top.addWidget(self.note, 1)
        top.addWidget(self.clear_btn)
        top.addWidget(self.compare_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 6)
        root.setSpacing(4)
        root.addLayout(top)

        if MATPLOTLIB_AVAILABLE:
            self.fig = Figure(figsize=(9, 4.6))
            self.canvas = FigureCanvas(self.fig)
            root.addWidget(self.canvas, 1)
        else:
            self.fig = self.canvas = None
            lbl = QLabel("matplotlib is not available, so the frame-yield chart "
                         "cannot be drawn.")
            lbl.setObjectName("placeholder")
            lbl.setWordWrap(True)
            root.addWidget(lbl, 1)

        self.group_combo.currentIndexChanged.connect(self._redraw)
        self.axle_combo.currentIndexChanged.connect(self._redraw)
        self.compare_btn.clicked.connect(self._pick_compare)
        self.clear_btn.clicked.connect(self._clear_compare)
        self._redraw()

    # ---- helpers -----------------------------------------------------------
    def _group(self):
        return "axle" if self.group_combo.currentText() == GROUP_AXLE else "unit"

    def _axle_filter(self):
        if self._group() != "axle":
            return None
        return self.axle_combo.currentData()      # None for "All axles"

    def _rows(self, groove, diag, group, axle_filter):
        if group == "axle":
            return compute.per_axle(diag, axle_filter)
        return compute.per_unit(groove)

    def _labels(self, group):
        if group == "axle":
            return ("Frame yield per wheel & axle — algo capture window",
                    "assigned = LMI frames in the algo's per-axle window; with groove "
                    "= frames carrying ≥1 profile",
                    "LMI frames per axle-wheel")
        return ("Frame yield per wheel (LMI unit) — whole scan",
                "a frame counts if ≥1 of its 10 depth slots has a real value "
                "(−1.8e308 = none)",
                "LMI frames per unit")

    def _subtitle(self, group, groove, diag):
        sid = compute.scan_id(diag, groove) or "—"
        _t, note, _u = self._labels(group)
        return "Scan %s  ·  %s" % (sid, note)

    # ---- drawing -----------------------------------------------------------
    def _redraw(self, *_):
        if self._closing or self.canvas is None:
            return
        group = self._group()
        # the Axle filter only makes sense per-axle and when not comparing
        self.axle_combo.setEnabled(group == "axle" and self._compare_root is None)
        title, _note, unit_word = self._labels(group)
        try:
            if self._compare_root is not None:
                cg, cd = compute.resolve_scan(self._compare_root)
                left = self._rows(self.groove_folder, self.diag_folder, group, None)
                right = self._rows(cg, cd, group, None)
                lt = "This scan  ·  %s" % (compute.scan_id(self.diag_folder,
                                                               self.groove_folder) or "—")
                rt = "Compare  ·  %s" % (compute.scan_id(cd, cg) or
                                             os.path.basename(self._compare_root))
                plot.draw_compare(self.fig, left, right, lt, rt, unit_word)
                self.note.setText("Comparing two scans — %s view" %
                                  ("per axle" if group == "axle" else "per unit"))
            else:
                rows = self._rows(self.groove_folder, self.diag_folder, group,
                                  self._axle_filter())
                sub = self._subtitle(group, self.groove_folder, self.diag_folder)
                plot.draw_yield(self.fig, rows, title, sub, unit_word)
                self.note.setText("")
            self.canvas.draw_idle()
        except Exception:
            traceback.print_exc()

    # ---- compare -----------------------------------------------------------
    def _pick_compare(self):
        start = os.path.dirname(self.scan_folder) if self.scan_folder else ""
        folder = QFileDialog.getExistingDirectory(
            self, "Pick a scan folder to compare", start)
        if not folder:
            return
        cg, cd = compute.resolve_scan(folder)
        if not cg and not cd:
            self.note.setText("That folder has no tire_laser_groove_data / diagnostics "
                              "— nothing to compare.")
            return
        self._compare_root = os.path.abspath(folder)
        self.clear_btn.setVisible(True)
        self._redraw()

    def _clear_compare(self):
        self._compare_root = None
        self.clear_btn.setVisible(False)
        self._redraw()

    def shutdown(self):
        self._closing = True
        try:
            if self.fig is not None:
                self.fig.clear()
        except Exception:
            pass
