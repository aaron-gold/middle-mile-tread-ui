"""Small Qt helpers shared by the diagnostics views (no main_window dependency).

info_page   a centred 'why this view is empty' placeholder (same look as the app's).
load_rgb    decode a regular PNG/JPG (the algo visualisations and any stitched image)
            to contiguous uint8 arrays for ZoomPanView. These are ordinary images, NOT
            the inverted Artemis TIFs, so they are read straight (no 255- inversion).
"""

import os

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from mm_viewer.common import lp_isfile


def info_page(head, text):
    """A tab/page that states why a view is not available - never a blank page."""
    ph = QWidget()
    lay = QVBoxLayout(ph)
    lbl = QLabel("<b>%s</b><br><br>%s" % (head, text))
    lbl.setObjectName("placeholder")
    lbl.setWordWrap(True)
    lbl.setTextFormat(Qt.TextFormat.RichText)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setMaximumWidth(900)
    lay.addStretch()
    lay.addWidget(lbl, 0, Qt.AlignmentFlag.AlignHCenter)
    lay.addStretch()
    return ph


def load_rgb(path):
    """
    Decode an ordinary image file to (display_rgb uint8 HxWx3, gray uint8 HxW) for
    ZoomPanView.set_image. Returns (None, None) on failure. Handles non-ASCII paths
    and palette/alpha/16-bit inputs. NOT for Artemis TIFs (those need inversion).
    """
    if not path or not lp_isfile(path):
        return None, None
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            rgb = np.ascontiguousarray(np.asarray(im, dtype=np.uint8))
    except Exception:
        # last resort: OpenCV via imdecode (non-ASCII path safe)
        try:
            import cv2
            buf = np.fromfile(path, dtype=np.uint8)
            bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if bgr is None:
                return None, None
            rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        except Exception:
            return None, None
    gray = np.ascontiguousarray((rgb.astype(np.float32).mean(axis=2)).astype(np.uint8))
    return rgb, gray


def base_name(path):
    return os.path.basename(path) if path else ""
