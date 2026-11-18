"""Show a stitched side image when the scan carries one (it is never generated here).

Per the agreed behaviour: display a stitched image FILE if present; otherwise say so
and offer an 'Open image…' button so a file named differently can still be shown.
Detection (loader.find_stitched_images) looks at the scan root and a stitched/helios
sub-folder only - the camera frame folders are never trawled.
"""

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
)

from mm_viewer.widgets.controls import ArrowComboBox
from mm_viewer.widgets.layout import allow_shrink, field_label
from mm_viewer.widgets.zoom_view import ZoomPanView
from mm_viewer.diagnostics.ui import load_rgb


class StitchedImagePanel(QWidget):
    """Source selector (when several found) + a zoom/pan view + Open image…."""

    EXTS = "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;All files (*)"

    def __init__(self, images=None, scan_folder=None, parent=None):
        super().__init__(parent)
        self.scan_folder = scan_folder
        self.images = list(images or [])

        self.src_combo = ArrowComboBox()
        self.src_combo.setMinimumWidth(320)
        self.open_btn = QPushButton("Open image…")
        self.open_btn.setObjectName("ghostBtn")
        self.open_btn.setToolTip("Pick a Helios / stitched image file to display.")
        self.caption = QLabel("")
        self.caption.setObjectName("hintText")
        allow_shrink(self.caption, 160)

        top = QHBoxLayout()
        top.setContentsMargins(20, 10, 20, 4)
        top.setSpacing(10)
        top.addWidget(field_label("Helios image"))
        top.addWidget(self.src_combo)
        top.addWidget(self.open_btn)
        top.addWidget(self.caption, 1)

        self.view = ZoomPanView()
        self.view.set_mode(ZoomPanView.MODE_PAN)

        self.empty = QLabel(
            "No stitched image found in this scan.\n\n"
            "The viewer shows a stitched side image when the scan carries one — a file "
            "at the scan root whose name contains ‘stitch’, ‘pano’, ‘mosaic’, ‘helios’ "
            "or ‘wall’, or any image in a ‘stitched’ / ‘helios’ sub-folder.\n\n"
            "Use ‘Open image…’ to display a file named differently.")
        self.empty.setObjectName("placeholder")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 6)
        root.setSpacing(4)
        root.addLayout(top)
        root.addWidget(self.view, 1)
        root.addWidget(self.empty, 1)

        for p in self.images:
            self.src_combo.addItem(os.path.basename(p))
        self.src_combo.currentIndexChanged.connect(self._on_src)
        self.open_btn.clicked.connect(self._open)

        self._apply_state()
        if self.images:
            self._on_src(0)

    def _apply_state(self):
        has = bool(self.images)
        self.src_combo.setVisible(has)
        self.view.setVisible(has)
        self.empty.setVisible(not has)

    def _on_src(self, i):
        if not (0 <= i < len(self.images)):
            return
        path = self.images[i]
        rgb, gray = load_rgb(path)
        if rgb is None:
            self.caption.setText("Could not open  %s" % os.path.basename(path))
            self.view.clear_image()
            return
        self.view.set_image(rgb, gray)
        self.view.fit_image()
        self.caption.setText("%s    ·    %d×%d"
                             % (os.path.basename(path), rgb.shape[1], rgb.shape[0]))

    def _open(self):
        start = self.scan_folder or ""
        path, _f = QFileDialog.getOpenFileName(
            self, "Select a stitched image", start, self.EXTS)
        if not path:
            return
        path = os.path.abspath(path)
        if path not in self.images:
            self.images.append(path)
            self.src_combo.addItem(os.path.basename(path))
        self._apply_state()
        self.src_combo.setCurrentIndex(self.images.index(path))

    def shutdown(self):
        try:
            self.view.clear_image()
        except RuntimeError:
            pass
