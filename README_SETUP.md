# Middle Mile Viewer v1.15

Desktop viewer for one UVeye Middle Mile (Artemis) scan: tread depth per groove, the
laser profiles behind it, the camera frames, and how the wheel assembler split the scan
into axles. It only reads the scan folder - nothing is written into it.

Built from git tag v1.15.

## What it does
Open one scan folder (its name is the scan ID) or a scan `.tar`. The top line shows
what was found and the scan ID; the banner below summarises the axle split (hover for
the full notes). All views are under the **Artemis** tab:

| Tab | What it shows |
|---|---|
| **Laser Grooves** | The vehicle map: every wheel with each measured groove coloured by depth (red below the detection limit, amber 1.6–3.2 mm, green ≥ 3.2 mm), inner and outer tyres of duals, and a profile thumbnail with the depth of each groove. Click a wheel for its profiles and depth chart. The Trash row holds the profiles the wheel assembler gave no axle, with how many are valid. Export CSV writes every groove. |
| **Tread mapping** | Tread camera frames per wheel, picked on a vehicle map. |
| **Tire Wall (OCR)** | Tire-wall (OCR) camera frames per wheel, picked on a vehicle map. |
| **Tread + Laser** | Tread frames played together with the nearest laser profile in time. |
| **Wheel Assembler** | A **View** dropdown at the top switches between: *Triggers & axle split* (trigger log and axle windows) and *Laser timing* (each wheel's profiles against its gate rise/fall and the 400 ms acquisition window). |

Atlas / Helios tabs appear when those cameras are in the scan. A single sub-folder
(one camera folder, `wheel_assembler`, `tire_laser_groove_data` or `diagnostics`) can
also be opened on its own.

## Setup
1. Install **Python 3.11** from python.org (tick "Add to PATH"). Check: `py -3.11 -V`
2. Unzip to a **short path**, e.g. `C:\MiddleMile_GUI`. Keep the layout: the `.py`
   files plus the folders `assets\`, `mm_viewer\` and `uveye_common\` side by side.
   Do not rename anything (one file name contains spaces on purpose).
3. In that folder, in a terminal:

       py -3.11 -m venv .venv
       .venv\Scripts\python -m pip install -r requirements.txt
       .venv\Scripts\python MiddleMile_GUI.py

   Optional: `.venv\Scripts\python MiddleMile_GUI.py "<scan folder or .tar>"` opens a
   scan straight away.

**PyCharm:** File > Open the folder > Settings > Python Interpreter > Add Local
Interpreter > Virtualenv (base: Python 3.11) > then in its terminal
`python -m pip install -r requirements.txt` and run `MiddleMile_GUI.py`.
