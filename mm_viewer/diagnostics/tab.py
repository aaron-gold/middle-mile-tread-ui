"""The Diagnostics sub-tab: a View dropdown over the comparison and the algo images.

  Comparison (tables + plots)   algo-vs-LMI per groove, per axle, with Excel export
  Algo images                   browse the algo's step-by-step PNGs per axle

Built only when a diagnostics tree is present (main_window gates on it). Its axle list
is discovered once here (cheap - small CSV/JSON reads) and shared with the image
browser; the comparison panel does its own heavier read (the raw LMI frames) off the
GUI thread. shutdown() forwards to both child panels, so the main window's tab-clear
reaches the comparison worker.
"""

import traceback

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from mm_viewer.widgets.controls import ArrowComboBox
from mm_viewer.widgets.layout import allow_shrink, field_label
from mm_viewer.diagnostics import loader
from mm_viewer.diagnostics.ui import info_page
from mm_viewer.diagnostics.panel import DiagnosticsComparePanel
from mm_viewer.diagnostics.images import DiagImagesPanel
from mm_viewer.frame_yield.panel import FrameYieldPanel


DIAG_VIEWS = ("Comparison (tables + plots)", "Algo images", "Frame yield")


class DiagnosticsTab(QWidget):
    def __init__(self, diag_folder, groove_folder=None, parent=None):
        super().__init__(parent)
        self.diag_folder = diag_folder
        self.groove_folder = groove_folder
        try:
            self.axles = loader.discover_axles(diag_folder)
        except Exception:
            traceback.print_exc()
            self.axles = []

        self.view_combo = ArrowComboBox()
        self.view_combo.addItems(list(DIAG_VIEWS))
        self.view_combo.setMinimumWidth(220)
        self.view_combo.setToolTip(
            "Comparison — the algo groove depths against the raw LMI, per axle.\n"
            "Algo images — the algo's own step-by-step diagnostic pictures.\n"
            "Frame yield — frames assigned vs with a groove, per axle/wheel.")
        self.view_note = QLabel("")
        self.view_note.setObjectName("hintText")
        allow_shrink(self.view_note, 140)

        top = QHBoxLayout()
        top.setContentsMargins(20, 10, 20, 0)
        top.setSpacing(10)
        top.addWidget(field_label("View"))
        top.addWidget(self.view_combo)
        top.addWidget(self.view_note, 1)

        self.stack = QStackedWidget()
        if self.axles:
            self.compare = DiagnosticsComparePanel(diag_folder, groove_folder)
            self.images = DiagImagesPanel(self.axles)
            self.yield_panel = FrameYieldPanel(groove_folder=groove_folder,
                                               diag_folder=diag_folder,
                                               default_group="axle")
            self.stack.addWidget(self.compare)
            self.stack.addWidget(self.images)
            self.stack.addWidget(self.yield_panel)
        else:
            self.compare = self.images = self.yield_panel = None
            self.stack.addWidget(info_page(
                "No algo groove data in this diagnostics folder",
                "The diagnostics tree carries no "
                "<code>*_finalized_groove_depths.csv</code>, so there is nothing to "
                "compare or plot."))
            self.view_combo.setEnabled(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addLayout(top)
        root.addWidget(self.stack, 1)

        self.view_combo.currentIndexChanged.connect(self._on_view)
        self._on_view(0)

    def _on_view(self, i):
        self.stack.setCurrentIndex(i if self.axles else 0)
        if not self.axles:
            self.view_note.setText("")
        elif i == 0:
            self.view_note.setText("Algo groove depths vs raw LMI  ·  export per scan")
        elif i == 1:
            self.view_note.setText("The algo's own diagnostic images, per axle")
        else:
            self.view_note.setText("Frames assigned vs with a groove, per axle/wheel")

    def shutdown(self):
        for p in (self.compare, self.images, self.yield_panel):
            if p is not None:
                try:
                    p.shutdown()
                except RuntimeError:
                    pass
