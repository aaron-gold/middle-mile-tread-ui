

import os
import io
import re
import sys
import csv
import glob
import json
import math
import warnings
import contextlib
import subprocess
import traceback
import types
import time
from pathlib import Path
from datetime import datetime
from collections import OrderedDict

import numpy as np
import cv2

try:
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False
    Image = None   # optional dependency missing

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QSlider, QVBoxLayout, QTabWidget, QHBoxLayout,
    QPushButton, QLineEdit, QGraphicsView, QGraphicsScene,
    QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsLineItem, QMessageBox,
    QDialog, QComboBox, QFileDialog, QTextEdit, QFrame, QSizePolicy,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QCheckBox, QSplitter, QGridLayout, QScrollArea, QStackedWidget, QLayout,
    QListWidget, QListWidgetItem, QDoubleSpinBox
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, pyqtProperty, QRectF, QPointF, QRect, QPoint, QLineF, QTimer,
    QSize, QThread, QPropertyAnimation, QEasingCurve
)
from PyQt6.QtGui import (
    QPixmap, QImage, QKeySequence, QShortcut, QPen, QColor, QBrush, QIcon, QPainter,
    QFont, QFontMetrics, QPainterPath, QLinearGradient
)

# ---- matplotlib (interactive analytics windows) ----
MATPLOTLIB_AVAILABLE = True
try:
    import matplotlib
    matplotlib.use("QtAgg")

    warnings.filterwarnings("ignore", message=r".*[Tt]ight layout not applied.*",
                            category=UserWarning)
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D
    # reads his saved system_pulse_diagram.png onto the wheel-assembler canvas
    import matplotlib.image as mpimg
except Exception:
    MATPLOTLIB_AVAILABLE = False
    matplotlib = FigureCanvas = NavigationToolbar = Figure = Line2D = mpimg = None   # optional dependency missing

# pandas only needed for the groove-depth plot
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except Exception:
    PANDAS_AVAILABLE = False
    pd = None   # optional dependency missing


try:
    from scipy.signal import savgol_filter
    SCIPY_AVAILABLE = True
except Exception:
    SCIPY_AVAILABLE = False
    savgol_filter = None   # optional dependency missing


_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app folder, one up from mm_viewer\
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
try:
    import mm_axle_classifier_v15 as axle_mod       # v1.5: shared frames
    AXLE_AVAILABLE = True
except Exception:
    traceback.print_exc()
    axle_mod = None
    AXLE_AVAILABLE = False


if AXLE_AVAILABLE:
    lp = axle_mod.lp
    unlp = axle_mod.unlp
    lp_open = axle_mod.lp_open
    lp_isfile = axle_mod.lp_isfile
    lp_isdir = axle_mod.lp_isdir
    lp_exists = axle_mod.lp_exists
    lp_stat = axle_mod.lp_stat
    lp_listdir = axle_mod.lp_listdir
    lp_walk = axle_mod.lp_walk
    lp_listmatch = axle_mod.lp_listmatch
    lp_long_paths = axle_mod.long_paths
else:                                       # pragma: no cover - degraded mode
    def lp(path):
        return path

    def unlp(path):
        return path

    lp_open = open

    def lp_isfile(path):
        return bool(path) and os.path.isfile(path)

    def lp_isdir(path):
        return bool(path) and os.path.isdir(path)

    def lp_exists(path):
        return bool(path) and os.path.exists(path)

    def lp_stat(path):
        return os.stat(path)

    def lp_listdir(path):
        return os.listdir(path)

    def lp_walk(top):
        return os.walk(top)

    def lp_listmatch(folder, pattern):
        import fnmatch as _fn
        try:
            return [os.path.join(folder, n) for n in os.listdir(folder)
                    if _fn.fnmatch(n, pattern)]
        except OSError:
            return []

    def lp_long_paths(root, limit=260):
        return []


APP_TITLE = "UVeye Middle Mile Viewer 1.15"
IMAGE_EXTS = (".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp")
