"""uveye_common - code shared by Itay's UVeye tools (MM Viewer, IQ toolkit, ...).

    tif        read_true(): frames with TRUE pixel values (Artemis TIF inversion trap)
    scan_id    parse_folder_name(), from_path()
    qt_guard   install_exception_guard(): binding-agnostic (PyQt6 / PySide6)

Install (once per venv, editable - edits here are live):
    "<venv>\\Scripts\\python.exe" -m pip install -e C:\\Users\\itay.f\\UVeye\\02_tooling\\python\\uveye_common
A dist zip ships a copy of the uveye_common\\ folder beside the app's launcher instead.
"""
__version__ = "0.1.0"
