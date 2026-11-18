"""Middle Mile Viewer - package form of MiddleMile_GUI v1.8 (split 2026-10-18 = v1.9; v1.10 uses uveye_common).

Run the app:      .venv\\Scripts\\python.exe MiddleMile_GUI.py      (launcher beside this folder)
Build the exe:    .venv\\Scripts\\python.exe MiddleMile_GUI.py --build-exe

Where things are (module = one banner section of the old single file):
  common            imports, optional deps, long-path helpers, APP_TITLE
  theme             colour tokens, resource paths, LIGHT_QSS
  config            camera-group classification
  scan_files        scan ID, frame ordering          io_tif     image loading (TIF polarity)
  analytics         raw-gray stats window
  widgets\\          controls, zoom view, layout helpers, lazy tab page, frame viewer/selector
  vehicle_map\\      wheel card, vehicle diagram, map panel
  grooves\\          LMI groove loader, worker, profile inspector, GrooveDepthPanel (+ 4 mixins)
  wheel_assembler\\  triggers, timing plots, pulse diagram, WheelAssemblerPanel (+ 1 mixin),
                    WheelAssemblerTab (View dropdown: triggers | laser timing)
  tread_laser\\      tread + laser timeline and viewer
  groove_map\\       stats, groove position (v1.8), summary, wheel detail, status, views, tab
  laser_grooves\\    Laser Grooves tab: algo groove depths per wheel, its profiles, Trash
  timing\\           laser timing view: gate dwell, profiles vs gate edges (+400 ms window)
  input_model  loading  archives  main_window  app

`import mm_viewer` also exposes every top-level name of every module here, exactly as the
single file did (mm_viewer.MainWindow, mm_viewer.groove_wheel_summary, ...), so the test
suites can use the package like the old module. To REPLACE a name for a test (e.g. a fake
QMessageBox), use patch() - plain assignment would only change this flat view.
"""
from importlib import import_module as _import_module
import os as _os

with open(_os.path.join(_os.path.dirname(__file__), "_load_order.txt"), encoding="utf-8") as _f:
    _ORDER = [ln.strip() for ln in _f if ln.strip()]

_MODULES = [_import_module(f"{__name__}.{_m}") for _m in _ORDER]

for _mod in _MODULES:
    for _k, _v in vars(_mod).items():
        if not (_k.startswith("__") and _k.endswith("__")):
            globals()[_k] = _v


def patch(name, value):
    """Replace a top-level name everywhere it is bound (every module + this flat view)."""
    hit = 0
    for mod in _MODULES:
        if name in vars(mod):
            setattr(mod, name, value)
            hit += 1
    if not hit:
        raise AttributeError(f"mm_viewer has no top-level name {name!r}")
    globals()[name] = value
    return hit
