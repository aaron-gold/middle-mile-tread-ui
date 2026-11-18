"""UVeye Middle Mile Viewer - launcher (v1.9+: the code lives in the mm_viewer\\ package;
v1.10+ also needs uveye_common - installed in .venv, or a uveye_common\\ folder beside this file).

    .venv\\Scripts\\python.exe MiddleMile_GUI.py [SCAN_FOLDER_OR_TAR]
    .venv\\Scripts\\python.exe MiddleMile_GUI.py --build-exe

Up to v1.8 this file WAS the whole app (14,791 lines). Old versions: git tags v1.0 ... v1.8
(git log --oneline --decorate). mm_axle_classifier_v15.py must stay beside this file.
"""
from mm_viewer.app import main

if __name__ == "__main__":
    main()
