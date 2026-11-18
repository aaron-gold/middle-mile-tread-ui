"""Browse the algo's step-by-step diagnostic images (the PNGs it writes per axle).

A read-only viewer: pick an axle, pick one of its images (split_segments, groove
detection, scan summary, finalised grooves, …), pan/zoom it. This is the visual side
of "show the output of the algo", beside the numeric comparison.
"""

import os

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from mm_viewer.widgets.controls import ArrowComboBox
from mm_viewer.widgets.layout import allow_shrink, field_label
from mm_viewer.widgets.zoom_view import ZoomPanView
from mm_viewer.diagnostics import loader
from mm_viewer.diagnostics.compare import axle_title
from mm_viewer.diagnostics.ui import load_rgb


class DiagImagesPanel(QWidget):
    """Axle selector + image selector + a zoom/pan image view."""

    def __init__(self, axles, parent=None):
        super().__init__(parent)
        self.axles = list(axles or [])
        self._images = []                       # [(label, path)] for the current axle

        self.axle_combo = ArrowComboBox()
        for a in self.axles:
            self.axle_combo.addItem("%s   ·   %d images"
                                    % (axle_title(a), a.get("n_images", 0)))
        self.axle_combo.setMinimumWidth(200)
        self.img_combo = ArrowComboBox()
        self.img_combo.setMinimumWidth(260)
        self.caption = QLabel("")
        self.caption.setObjectName("hintText")
        allow_shrink(self.caption, 140)

        top = QHBoxLayout()
        top.setContentsMargins(20, 10, 20, 4)
        top.setSpacing(10)
        top.addWidget(field_label("Axle"))
        top.addWidget(self.axle_combo)
        top.addSpacing(12)
        top.addWidget(field_label("Image"))
        top.addWidget(self.img_combo)
        top.addWidget(self.caption, 1)

        self.view = ZoomPanView()
        self.view.set_mode(ZoomPanView.MODE_PAN)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 6)
        root.setSpacing(4)
        root.addLayout(top)
        root.addWidget(self.view, 1)

        self.axle_combo.currentIndexChanged.connect(self._on_axle)
        self.img_combo.currentIndexChanged.connect(self._on_image)
        if self.axles:
            self._on_axle(0)

    def _on_axle(self, i):
        if not (0 <= i < len(self.axles)):
            return
        self._images = loader.axle_images(self.axles[i]["folder"])
        self.img_combo.blockSignals(True)
        self.img_combo.clear()
        for label, _p in self._images:
            self.img_combo.addItem(label)
        self.img_combo.blockSignals(False)
        if self._images:
            # open on the finalised-grooves image when present (the headline result)
            start = next((k for k, (lab, _p) in enumerate(self._images)
                          if "finalized" in lab.lower() or "finalised" in lab.lower()),
                         0)
            self.img_combo.setCurrentIndex(start)
            self._on_image(start)
        else:
            self.view.clear_image()
            self.caption.setText("This axle has no diagnostic images.")

    def _on_image(self, i):
        if not (0 <= i < len(self._images)):
            return
        label, path = self._images[i]
        rgb, gray = load_rgb(path)
        if rgb is None:
            self.view.clear_image()
            self.caption.setText("Could not open  %s" % os.path.basename(path))
            return
        self.view.set_image(rgb, gray)
        self.view.fit_image()
        self.caption.setText("%s    ·    %s" % (os.path.basename(path),
                                                "%d×%d" % (rgb.shape[1], rgb.shape[0])))

    def shutdown(self):
        try:
            self.view.clear_image()
        except RuntimeError:
            pass
