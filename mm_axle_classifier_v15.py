"""
Axle-classification adapter for the Middle Mile Viewer.

v15 (2026-10-05) - copy of mm_axle_classifier.py for MiddleMile_GUI_v1.5.py; the
original is unchanged and still serves v1.4.
  * SHARED FRAMES. His classifier now puts the same frame on more than one axle
    when two axles' windows overlap (seen on 2026-09-15T09-55-32: 57 front-tread
    frames per side on Axle 1 AND Axle 2, 4.11-5.88 s from vehicle entry). The
    manifest carries one row per (file, axle). v1.4 kept the first row and dropped
    the rest, so Axle 2 lost 62% of its front tread with only a warning.
    AxleIndex now keeps every axle a file belongs to:
        assign[path]  the PRIMARY bucket (lowest axle number) - unchanged meaning
                      for every caller that wants one answer
        also[path]    the other axle bucket(s) the same file is on
        pairs()       every (path, bucket) membership - what per-axle views iterate
    A file on an axle AND in Trash is still a contradiction: the axle wins and it is
    counted as a conflict, never silently.

OWNERSHIP - READ THIS BEFORE EDITING
------------------------------------
The axle classification is NOT owned by this file. All of it lives in the system
engineer's own scripts, of which there are now TWO:

    WHEEL_ASSEMBLER_MODULE_SPLIT_BY_AXEL.py   WheelAssemblerComplete
        Host counter in MILLISECONDS. The format every scan on disk carries today.

    WHEEL ASSEMBLER MODULE_SPLIT_BY_AXEL.py   WheelAssemblerAbsoluteSyncPipeline
        Host_Timestamp_usec, absolute-sync anchors, microsecond windows. Added
        2026-09-01, for the log format the firmware is moving to.

Neither is imported normally (each ends in a block that runs a hard-coded scan on
his own machine), neither is edited, and both are byte-for-byte copies of what he
handed over. `load_engineer_module()` reads the source, executes ONLY the part above
that script's execution marker, and hands back the live class. Anything he changes in
either file is picked up automatically.

WHICH ONE RUNS is the only decision made here, and it is made from the scan's own
trigger-log columns (`detect_log_format` / `pipeline_for`), never from a preference.
That matters because the two are not interchangeable: his microsecond pipeline's
window pads are fixed usec literals, so on a millisecond log they become +/-100 s and
2500 s. Measured on scan 2026-08-31T11-16-05, running it there puts 2,668 of 2,736
files in Trash and classifies no laser file at all. `AxleIndex.pipeline`,
`.pipeline_class`, `.time_unit` and `.host_per_ms` record what actually ran, and the
viewer shows it.

SINCE 2026-09-03 a complete scan ships his classifier's own answer with it, as
`Scan_Output_Classified/scan_classification_manifest.csv`. When that file is there it
is READ instead of re-running anything (`index_from_manifest`), because it is the
split the scan was actually processed with; a local re-run can only agree with it or
contradict it. See the MANIFEST CSV block below for what it does and does not carry.
Everything under here still applies to the scans that do not carry one.

The viewer runs HIS methods, unmodified, and takes his answer as final:

    parse_triggers / _build_axle_windows / _match_host / _match_ocr   his, inherited
    scan_files                                                        his, inherited
    execute_copy                                                      replaced
    process_and_execute                                               replaced
    plot_and_show_system_diagram                                      replaced

Only writers and blockers are replaced. `execute_copy` and `process_and_execute`
write into the scan folder - the latter also COPIES every classified file and
INVERTS every TIF it copies - and none of that may happen because a scan was opened
for viewing. `plot_and_show_system_diagram` calls `plt.show()`, which opens a second
non-Qt window and blocks the event loop; his diagram is instead drawn onto the tab's
own canvas by the viewer's ENGINEER SYSTEM DIAGRAM block, which runs his separate
`Vehicle_Per_Axis_Plot.py` verbatim. The viewer classifies in memory and writes
nothing until the user asks; `export_tree` then reproduces his destination layout:

    <dest>/<Axle_N>/<Side>/<MediaType>/<basename>      (Trash -> <dest>/Trash/)

The index below is built straight from his `file_manifest` - one entry per item he
put in it, with the axle and side he assigned. Nothing is added, removed or
re-decided. Consequences of that fidelity, all his to change, none patched here:

  * files his scan_files never reaches are simply absent from the VIEWER: every
    `*_profile.csv` (no `timestamp_msec` column, so his reader skips it),
    `full_scan.rec`, `triggers_data.csv` and every Atlas / Helios camera folder.
    They are NOT absent from the export any more - see export_tree, which carries
    them over verbatim under `Unclassified/` so the export holds the whole scan.
  * `scan_grooves_full.csv` carries a timestamp column, so his loop treats it as a
    frame and assigns it by its first row.
  * on a 2-axle scan only `front_*` windows exist, so REAR laser/tread files match
    no window and go to Trash. Surfaced as a coverage warning, not corrected.
  * his manifest is keyed on basename, and the four LMI folders (and the four tread
    folders) share every basename. His destination layout therefore let a later copy
    replace an earlier one - 1,009 of 2,956 classified files on a real scan. The
    export no longer reproduces that layout: it appends the SOURCE FOLDER as the
    last path element, so every destination is unique. His axle decisions are
    untouched; only where the bytes land changed.
  * M is taken from the busiest gate and every gate is then indexed to it, which
    raises IndexError when gates are unbalanced. Caught and re-raised as a
    ClassificationError so the viewer can flag the scan instead of dying.
"""

import os
import re
import csv
import fnmatch
import shutil
import importlib.util
from collections import defaultdict

# ------------------------------------------------------------------
# LONG PATHS ON WINDOWS, 2026-09-08
# ------------------------------------------------------------------
# MEASURED, not defensive. The 3-axle scan of 2026-09-07 sits at
#
#   ...\Wheel Assembler\07.09.2026\3 Axel\<scan id>\<scan id>        215 chars
#
# and its own files run past the Win32 MAX_PATH of 260:
#
#   Scan_Output_Classified\scan_classification_manifest.csv          271
#   tire_laser_groove_data\LMI_0_FRONT_LEFT\frame_100_grooves.csv    277
#
# LongPathsEnabled is 0 on the qualification laptop, so for every one of those
# os.path.isfile() answers FALSE and open() raises FileNotFoundError. Nothing reports
# an error: the manifest reads as "this scan carries no Scan_Output_Classified
# folder", the split silently falls back to a local re-run, and every groove CSV
# disappears from the Laser Grooves tab. A deep download folder is normal on a test
# day, so this is corrected rather than worked around.
#
# lp() returns the extended-length form of a path, which the Win32 API accepts up to
# 32,767 characters. It is applied ONLY at the moment of I/O; nothing is ever STORED
# in that form, because the axle index is keyed on absolute paths and a mix of
# prefixed and unprefixed keys would make the Laser Grooves axle filter match nothing
# while looking perfectly healthy. unlp() is what enforces that: every path that comes
# back out of a prefixed listing is stripped before it is returned or stored.
#
# The prefix carries a '?', which is a glob metacharacter - glob.glob() on a prefixed
# pattern searches for a directory literally named "?" and finds nothing. That is why
# the searches below are written on os.listdir / os.walk + fnmatch, not on glob.
#
# Every backslash below is spelled out through _BSL rather than written as an escape.
# The prefix is four characters of pure punctuation and one dropped escape turns it
# into a path that silently matches nothing, which is exactly the class of failure
# this block exists to remove.
_BSL = chr(92)                       # one backslash
_LP_PREFIX = _BSL + _BSL + "?" + _BSL       # the four characters  \\?\
_LP_UNC = _LP_PREFIX + "UNC" + os.sep
_UNC_ROOT = _BSL + _BSL              # a UNC path starts \\server\share
_DEVICE_ROOT = _BSL + _BSL + "." + _BSL     # \\.\ device namespace, left alone
# Below this length a path cannot reach MAX_PATH even after a filename is joined onto
# it, so the caller's own string is handed back untouched and nothing changes on the
# ordinary scans. 240 leaves room for the longest basename a scan carries
# (scan_classification_manifest.csv, 33) plus a separator.
_LP_LIMIT = 240


def lp(path):
    """
    `path` in the form Windows can open at any length. Unchanged off Windows, on a
    path that is already extended-length, and on anything short enough not to need it.
    """
    if os.name != "nt" or not path:
        return path
    try:
        p = os.fspath(path)
    except TypeError:
        return path
    if not isinstance(p, str):
        return path
    if p.startswith(_LP_PREFIX) or p.startswith(_DEVICE_ROOT):
        return p
    if len(p) < _LP_LIMIT and not p.startswith(_UNC_ROOT):
        return p
    try:
        full = os.path.abspath(p)               # the prefix requires a full path
    except Exception:
        return p
    if len(full) < _LP_LIMIT:
        return p
    if full.startswith(_UNC_ROOT):              # -> \\?\UNC\server\share
        return _LP_UNC + full[2:]
    return _LP_PREFIX + full


def unlp(path):
    """The ordinary form of a path that may carry the extended-length prefix."""
    if os.name != "nt" or not isinstance(path, str):
        return path
    if path.startswith(_LP_UNC):
        return _UNC_ROOT + path[len(_LP_UNC):]
    if path.startswith(_LP_PREFIX):
        return path[len(_LP_PREFIX):]
    return path


def lp_open(path, *args, **kwargs):
    """open() that survives MAX_PATH."""
    return open(lp(path), *args, **kwargs)


def lp_isfile(path):
    return bool(path) and os.path.isfile(lp(path))


def lp_isdir(path):
    return bool(path) and os.path.isdir(lp(path))


def lp_exists(path):
    return bool(path) and os.path.exists(lp(path))


def lp_stat(path):
    return os.stat(lp(path))


def lp_getsize(path):
    return os.path.getsize(lp(path))


def lp_listdir(path):
    """os.listdir() at any length. Returns names, so no prefix can leak out."""
    return os.listdir(lp(path))


def lp_walk(top):
    """os.walk() at any length, yielding ORDINARY paths - see the note on unlp()."""
    for root, dirs, files in os.walk(lp(top)):
        yield unlp(root), dirs, files


def lp_listmatch(folder, pattern):
    """
    Every entry of `folder` whose name matches `pattern`, as ordinary absolute paths.

    The stand-in for glob.glob() on one directory level. Written on listdir+fnmatch
    because a glob pattern cannot carry the extended-length prefix (its '?' is a
    metacharacter), and because the caller only ever needs one level.
    """
    out = []
    try:
        names = os.listdir(lp(folder))
    except OSError:
        return out
    for name in names:
        if fnmatch.fnmatch(name, pattern):
            out.append(os.path.join(folder, name))
    return out


def lp_find(root, name, max_hits=0):
    """
    Every file called `name` anywhere under `root`, as ordinary absolute paths. The
    stand-in for a recursive glob; `max_hits` stops the walk early.
    """
    hits = []
    low = (name or "").lower()
    for cur, _dirs, files in lp_walk(root):
        for f in files:
            if f.lower() == low:
                hits.append(os.path.join(cur, f))
                if max_hits and len(hits) >= max_hits:
                    return hits
    return hits


def lp_has_dir(root, pattern):
    """True when any directory anywhere under `root` matches `pattern`."""
    for _cur, dirs, _files in lp_walk(root):
        if any(fnmatch.fnmatch(d, pattern) for d in dirs):
            return True
    return False


# Enough to say "this scan is full of them" without building a list nobody reads.
LONG_PATH_REPORT_CAP = 5000


def long_paths(root, limit=260):
    """
    Every file under `root` at or over `limit` characters, for the viewer's banner.

    Reported, never corrected: the files ARE readable now, and someone who moves the
    scan to a shorter folder should know why it was worth doing.
    """
    out = []
    for cur, _dirs, files in lp_walk(root):
        for f in files:
            p = os.path.join(cur, f)
            if len(p) >= limit:
                out.append(p)
                if len(out) >= LONG_PATH_REPORT_CAP:
                    return out
    return out


ENGINEER_SCRIPT = "WHEEL_ASSEMBLER_MODULE_SPLIT_BY_AXEL.py"

# ------------------------------------------------------------------
# TWO engineer pipelines, 2026-09-01
# ------------------------------------------------------------------
# The system engineer issued a second, absolute-sync pipeline. It is NOT a drop-in
# replacement: it reads a log format the current firmware does not emit yet.
#
#     ENGINEER_SCRIPT          WheelAssemblerComplete            Host in MILLISECONDS
#     ENGINEER_SCRIPT_SYNC     WheelAssemblerAbsoluteSyncPipeline  Host_Timestamp_usec
#
# Both files are byte-identical copies of what he handed over, and neither is edited
# here - see the module docstring. What the viewer decides is only WHICH ONE RUNS, and
# it decides that from the scan's own columns (detect_log_format), never from a guess:
#
#   * his sync pipeline hard-codes microsecond window pads (PAD_OCR_USEC = 100000,
#     CAM_WINDOW_USEC = 2500000). On a millisecond log those become +/-100 s and
#     2500 s, and its camera arithmetic adds a microsecond frame delta to a
#     millisecond anchor. Measured on scan 2026-08-31T11-16-05: 2,668 of 2,736 files
#     land in Trash and the laser loop matches NOTHING, because its groove-CSV column
#     probe ('Device_Timestamp_usec' / 'Host_Timestamp_usec' / 'Host') finds none of
#     the three in a file whose columns are sensor_timestamp_msec / timestamp_msec.
#   * on a scan that DOES carry the usec columns his pipeline is the correct one and
#     is the one that runs.
#
# So a millisecond scan keeps the millisecond pipeline and a microsecond scan gets the
# new one. AxleIndex.pipeline / .time_unit say which ran, and the viewer shows it.
ENGINEER_SCRIPT_SYNC = "WHEEL ASSEMBLER MODULE_SPLIT_BY_AXEL.py"

# His two scripts end their definitions with different markers, and the newer one's
# ("CONTROL") does not contain the older one's as a substring - so splitting on the old
# marker alone left the whole file in place and exec'd his hard-coded run against
# C:\Users\oz.s\Downloads\... on every launch. Both are honoured, first match wins.
EXEC_MARKERS = ("# --- EXECUTION CONTROL ---", "# --- EXECUTION ---")
EXEC_MARKER = EXEC_MARKERS[1]          # kept: the smoke suite asserts on this name

# The class each script defines, newest first. A script is accepted when exactly one
# of these is present above its marker.
ENGINEER_CLASSES = ("WheelAssemblerAbsoluteSyncPipeline", "WheelAssemblerComplete")

# Time unit of the Host column, and how many Host counts make one millisecond. Every
# window the engineer's scripts build is in these units, so anything downstream that
# needs milliseconds (the per-axle SRS bands) scales by host_per_ms rather than
# assuming one or the other.
UNIT_MSEC = "msec"
UNIT_USEC = "usec"
HOST_PER_MS = {UNIT_MSEC: 1.0, UNIT_USEC: 1000.0}

# The trigger-log column that tells the two formats apart. Its presence is the ONLY
# signal used - not the magnitude of the numbers, which a long-running controller
# could push into either range.
USEC_TRIGGER_COL = "Host_Timestamp_usec"

TRASH = "Trash"
# where the export puts everything the engineer's scan_files() never reaches -
# the Atlas cameras, the LMI *_profile.csv files, wheel_assembler/, full_scan.rec
UNCLASSIFIED = "Unclassified"

# the media_type strings his scan_files() writes into the manifest
MEDIA_LASER = "Laser"
MEDIA_TREAD = "Tread"
MEDIA_OCR = "OCR"

_FRAME_RE = re.compile(r"frame[_\-]?(\d+)", re.IGNORECASE)

# ------------------------------------------------------------------
# ALREADY-CLASSIFIED FOLDER TREES, 2026-09-01
# ------------------------------------------------------------------
# A scan can arrive already split by axle, and re-running the state machine on one is
# both pointless and wrong: the trigger log usually is not in the tree (the export
# carries it under Unclassified/), and the split on disk is the record - re-deriving
# it could silently disagree with the folders the user is looking at.
#
# TWO layouts are read, because two tools write them:
#
#   this viewer's export_tree()          <root>/Axle_1/Left/Tread/tr_front_l_01/f.tif
#                                        <root>/Trash/Tread/tr_rear_l_01/f.tif
#                                        <root>/Unclassified/<original path>
#   his process_and_execute()            <scan>/Scan_Output_Classified/Axle_1/Left/
#                                            Tread/f.tif
#                                        <scan>/Scan_Output_Classified/Trash/
#                                            tr_rear_l_01/f.tif
#
# The difference is only the camera-folder level (his layout drops it) and where the
# tree sits, so one reader covers both: everything under an Axle_N directory is that
# axle's, the first path element below it is the side when it names one, the next is
# the media type when it names one, and anything left is the camera folder.
#
# WHAT A TREE CANNOT CARRY, and is therefore not claimed:
#   * no gate pulses and no axle windows. axle_for_host() answers None for every row,
#     so the trigger table's Axle column stays "—" rather than inventing a window.
#   * no pipeline and no time unit: nothing ran. `source` says TREE and the viewer
#     prints that instead of naming one of his two scripts.
#   * a tree written by HIS script holds INVERTED TIFs (process_and_execute inverts
#     every TIF it copies); the viewer's export does not. Recorded as a warning,
#     never corrected here - the pixels on disk are the record.
AXLE_DIR_RE = re.compile(r"^axle[\s_\-]*(\d+)$", re.IGNORECASE)

# Directory names that mark a tree as classified rather than merely containing a
# folder called "Axle 1" - checked in addition to, never instead of, an Axle_N dir.
TREE_SIDES = ("left", "right")
TREE_MEDIA = {MEDIA_LASER.lower(): MEDIA_LASER,
              MEDIA_TREAD.lower(): MEDIA_TREAD,
              MEDIA_OCR.lower(): MEDIA_OCR}

# His writer's own output folder name, so a scan folder that simply CONTAINS his
# classified output is recognised without the user having to point inside it.
ENGINEER_OUTPUT_DIR = "Scan_Output_Classified"

# How deep below the folder the user opened a classified root may sit. 1 covers his
# Scan_Output_Classified/ inside a scan folder; deeper than that and a stray Axle_1
# somewhere in a scan would start being read as a classification.
TREE_SEARCH_DEPTH = 1

SOURCE_SCRIPT = "script"        # the engineer's state machine ran on this scan
SOURCE_TREE = "tree"            # read off an existing classified folder tree
SOURCE_MANIFEST = "manifest"    # read off his Scan_Output_Classified manifest CSV

# ------------------------------------------------------------------
# HIS MANIFEST CSV, 2026-09-03
# ------------------------------------------------------------------
# A complete scan now ships his classifier's own output next to the camera folders:
#
#     <scan>/Scan_Output_Classified/scan_classification_manifest.csv
#     <scan>/Scan_Output_Classified/system_pulse_diagram.png
#
# The CSV is his `file_manifest` written to disk - one row per file he classified,
# with the axle, side and media he assigned and the host timestamp he computed. It is
# the SAME object build_index() mirrors when his pipeline is run here, so reading it
# is not a second opinion: it is his answer, taken from the run that produced the
# scan instead of from a re-run on this machine.
#
# WHY IT IS PREFERRED over re-running his pipeline:
#   * it is the record. The re-run can only agree with it or disagree with it, and a
#     disagreement would mean the viewer showing a split the scan was not processed
#     with.
#   * it needs neither of his scripts on this machine, and cannot fail on a log
#     format a local copy of them predates.
#   * it is ~2 orders of magnitude faster than walking every groove CSV again.
# Measured on scan 2026-09-03T08-36-36 (2,632 rows): the manifest and a local
# classify_scan() run agree on all 2,632 files, with no file on either side only.
#
# WHAT IT CANNOT CARRY, and is therefore not claimed:
#   * no gate pulses and no axle windows - he writes neither into the CSV. As with a
#     folder tree, axle_for_host() answers None and the trigger table's Axle column
#     stays "-" rather than inventing a window from file timestamps.
#   * no pipeline identity: the CSV does not say which of his two classes wrote it.
#     `pipeline` stays None and `source` says MANIFEST, so nothing on screen names a
#     script that did not run here.
# The rows' own microsecond timestamps ARE kept, on AxleIndex.host_ts, because they
# are measurements he made - they are simply not turned into windows.
MANIFEST_NAME = "scan_classification_manifest.csv"

# His diagram, saved by the same writer at 15x17in / 300 dpi. Only located here; the
# viewer decides what to do with it.
ENGINEER_DIAGRAM_NAME = "system_pulse_diagram.png"

# The columns the reader needs. `original_relative_path` is the one that matters:
# `original_full_path` is the path on the PT ("/mnt/data/sessions/..."), which does
# not exist on this machine, so it is never used to open a file.
MANIFEST_REQUIRED_COLS = ("original_relative_path", "target_axle")
MANIFEST_AXLE_RE = re.compile(r"^axle[\s_\-]*(\d+)$", re.IGNORECASE)

# Files that live inside a folder his classifier walks but are WHOLE-SCAN artefacts,
# not frames, so their absence from the manifest is by design and not a gap:
#
#   full_scan.rec           the LMI unit's raw recording
#   scan_grooves_full.csv   the per-unit depth lookup table build_full_index() reads,
#                           which is deliberately never filtered by axle either
#
# Counting these as "missing from the manifest" would put a warning on every healthy
# scan, which is how a warning stops being read.
MANIFEST_NOT_A_FRAME = ("full_scan.rec", "scan_grooves_full.csv", "triggers_data.csv")


class ClassificationError(Exception):
    """Raised when the engineer's script cannot run on a scan."""

    def __init__(self, message, detail="", hint=""):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.hint = hint


# ------------------------------------------------------------------
# Loading the engineer's class without running his script
# ------------------------------------------------------------------
def script_search_dirs():
    """
    Where an engineer script is looked for, in order.

    Next to the viewer first, so a frozen build carries its own copy and cannot pick
    up a half-edited working file. His authoring folder is the fallback, so dropping
    an updated script there is enough to pick it up without a rebuild.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return [here,
            os.path.abspath(os.path.join(here, os.pardir,
                                         "Wheel Assembler", "Scripts"))]


def find_engineer_script(name):
    """Absolute path of one engineer script, or None."""
    for d in script_search_dirs():
        p = os.path.join(d, name)
        if lp_isfile(p):
            return p
    return None


def split_definitions(src):
    """
    The part of an engineer script ABOVE its execution block.

    Each of his scripts ends with a block that instantiates the pipeline against a
    hard-coded folder on his own machine and processes thousands of files. Importing
    normally would run that on every viewer launch, so the source is cut at whichever
    marker it uses. When it carries none, the whole file is returned and the caller's
    exec is what reports the problem - a script is never guessed at.
    """
    for marker in EXEC_MARKERS:
        if marker in src:
            return src.split(marker)[0], marker
    return src, None


def load_engineer_module(script_path=None, script_name=None):
    """
    Execute only the definitions in an engineer script and return the module.

    His file is opened read-only and never modified: the source is truncated at its
    execution marker and exec'd into a private module object.
    """
    name = script_name or ENGINEER_SCRIPT
    if script_path is None:
        script_path = find_engineer_script(name)
    if not script_path or not lp_isfile(script_path):
        raise ClassificationError(
            "Axle-classification script not found.",
            detail=script_path or name,
            hint=f"{name} must sit next to the viewer, or in "
                 f"..\\Wheel Assembler\\Scripts.")

    with lp_open(script_path, "r", encoding="utf-8", errors="replace") as fh:
        src = fh.read()

    head, marker = split_definitions(src)
    present = [c for c in ENGINEER_CLASSES if ("class %s" % c) in head]
    if not present:
        raise ClassificationError(
            "Axle-classification script has an unexpected layout.",
            detail="None of %s was found above %s."
                   % (", ".join(ENGINEER_CLASSES),
                      ("`%s`" % marker) if marker else "the end of the file"),
            hint="The viewer loads the pipeline class from the script; it was "
                 "renamed or moved.")
    if marker is None:
        raise ClassificationError(
            "Axle-classification script has no execution marker.",
            detail="Expected one of: " + ", ".join(EXEC_MARKERS),
            hint="Without it the viewer cannot tell his definitions from the run "
                 "block at the bottom, and loading the script would execute that "
                 "run against whatever folder it names.")

    spec = importlib.util.spec_from_loader("mm_engineer_axle", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = script_path
    try:
        exec(compile(head, script_path, "exec"), mod.__dict__)
    except Exception as exc:
        raise ClassificationError(
            "Axle-classification script could not be loaded.",
            detail=f"{type(exc).__name__}: {exc}",
            hint="The script has a syntax or import error above the execution marker.")
    mod.MM_PIPELINE_CLASS = present[0]
    return install_long_path_shims(mod)


# ------------------------------------------------------------------
# LONG PATHS INSIDE HIS SCRIPTS, 2026-09-08
# ------------------------------------------------------------------
# His two pipelines find their files with glob and read them with open / pd.read_csv,
# and every one of those calls fails past MAX_PATH exactly as the viewer's own did.
#
# The scan folder CANNOT simply be handed to him in extended-length form: the prefix
# contains a '?', glob reads that as a metacharacter, and his
#
#     glob.glob(os.path.join(main_dir, '**', 'triggers_data.csv'), recursive=True)
#
# would then match nothing at all - a worse failure than the one being fixed, because
# it looks like a scan with no trigger log.
#
# So the scan folder stays ordinary and the FUNCTIONS are handed in instead, the same
# way `self`, `plt` and his free globals are handed to his plot fragment: after his
# source is exec'd, the module's own `glob`, `open`, `os` and `pd` names are rebound
# to long-path-safe stand-ins. His code is not touched, not one byte of it is read
# differently, and every path it produces is an ordinary path.
#
# Only the calls that touch the disk are wrapped; everything else falls through to the
# real module by __getattr__, so a future revision of his script cannot end up with a
# quietly different os or pandas from the one it asked for.
class _LpGlob:
    """`glob` for his scripts: the same call, resolved past MAX_PATH."""

    @staticmethod
    def glob(pattern, recursive=False):
        return lp_glob(pattern, recursive=recursive)

    @staticmethod
    def iglob(pattern, recursive=False):
        return iter(lp_glob(pattern, recursive=recursive))

    def __getattr__(self, name):
        import glob as _real
        return getattr(_real, name)


class _LpOsPath:
    """
    `os.path` for his scripts. Only the calls that touch the disk are wrapped;
    join / basename / relpath / abspath are string arithmetic and pass straight
    through, so the paths he builds are byte-identical to the ones he built before.
    """

    def exists(self, path):
        return lp_exists(path)

    def isfile(self, path):
        return lp_isfile(path)

    def isdir(self, path):
        return lp_isdir(path)

    def getsize(self, path):
        return lp_getsize(path)

    def __getattr__(self, name):
        return getattr(os.path, name)


class _LpOs:
    """`os` for his scripts, carrying the wrapped os.path."""

    def __init__(self):
        self.path = _LpOsPath()

    def makedirs(self, name, *a, **k):
        return os.makedirs(lp(name), *a, **k)

    def listdir(self, path="."):
        return lp_listdir(path)

    def walk(self, top, *a, **k):
        return lp_walk(top)

    def __getattr__(self, name):
        return getattr(os, name)


class _LpPandas:
    """`pd` for his scripts: read_csv past MAX_PATH, everything else untouched."""

    def __init__(self, pd):
        self.__dict__["_pd"] = pd

    def read_csv(self, filepath_or_buffer, *a, **k):
        if isinstance(filepath_or_buffer, str):
            filepath_or_buffer = lp(filepath_or_buffer)
        return self.__dict__["_pd"].read_csv(filepath_or_buffer, *a, **k)

    def __getattr__(self, name):
        return getattr(self.__dict__["_pd"], name)


# FOLDER RENAME, 2026-09-23. The tire-wall cameras now write artemis_left_wall /
# artemis_right_wall instead of tire_wall_l_01 / tire_wall_r_01. Both of his scripts
# hardcode the old names (and derive the side from '_l_' / '_r_' in them), so on a
# new scan they would find no OCR folder at all - and the absolute-sync one also
# anchors EVERY camera's clock on the OCR frame 0 it could not find. His code is not
# edited; the glob his module is given simply lets a new folder answer to its old
# name. Only these two names are aliased, and only inside his module's glob.
LEGACY_DIR_ALIASES = {"artemis_left_wall": ("tire_wall_l_01",),
                      "artemis_right_wall": ("tire_wall_r_01",)}


def legacy_dir_aliases(name):
    """The pre-2026-09-23 name(s) a folder on disk also answers to, or ()."""
    return LEGACY_DIR_ALIASES.get((name or "").lower(), ())


def lp_glob(pattern, recursive=False):
    """
    glob.glob() that works past MAX_PATH, written on listdir + fnmatch.

    Supports what his scripts use and nothing more: '*' and '?' inside a component,
    and a '**' component under recursive=True meaning "this directory and every
    directory below it". Results are ORDINARY absolute paths, sorted.
    """
    if not pattern:
        return []
    text = str(pattern)
    parts = [q for q in re.split(r"[\\/]+", text) if q != ""]
    if not parts:
        return []
    magic = "*?["
    base_parts = []
    i = 0
    while i < len(parts) and not any(c in parts[i] for c in magic):
        base_parts.append(parts[i])
        i += 1
    comps = parts[i:]
    if base_parts:
        base = os.sep.join(base_parts)
        if len(base_parts) == 1 and base_parts[0].endswith(":"):
            base += os.sep                      # "C:" is not the root of C:
    else:
        base = "."
    if text[:1] in ("\\", "/") and not base.startswith(os.sep):
        base = os.sep + base
    return _lp_glob_match(base, comps, recursive)


def _lp_glob_match(base, comps, recursive):
    """One level of lp_glob(). `comps` are the still-unmatched path components."""
    if not comps:
        return [base] if lp_exists(base) else []
    head, rest = comps[0], comps[1:]
    if head == "**":
        if not recursive:
            head = "*"                          # glob's own non-recursive reading
        else:
            # '**' matches this directory and every directory below it
            seen = [base]
            for cur, dirs, _files in lp_walk(base):
                for d in dirs:
                    seen.append(os.path.join(cur, d))
            out = []
            for d in seen:
                out.extend(_lp_glob_match(d, rest, recursive))
            return sorted(set(out))
    out = []
    try:
        names = os.listdir(lp(base))
    except OSError:
        return out
    for name in names:
        if not (fnmatch.fnmatch(name, head)
                or any(fnmatch.fnmatch(a, head) for a in legacy_dir_aliases(name))):
            continue
        full = os.path.join(base, name)
        if rest:
            if lp_isdir(full):
                out.extend(_lp_glob_match(full, rest, recursive))
        else:
            out.append(full)
    return sorted(out)


def install_long_path_shims(mod):
    """
    Rebind a loaded engineer module's file-access names to long-path-safe ones.

    Called after his source is exec'd, so his own `import glob` / `import os` /
    `import pandas as pd` have already run and are simply replaced in the module's
    globals. Module globals are searched before builtins, so binding `open` here is
    what makes his bare `open(json_file)` calls safe too.

    A name his script never imported is not created, so a script that does not use
    pandas does not acquire a pandas.
    """
    if "glob" in mod.__dict__:
        mod.__dict__["glob"] = _LpGlob()
    if "os" in mod.__dict__:
        mod.__dict__["os"] = _LpOs()
    if "pd" in mod.__dict__:
        try:
            mod.__dict__["pd"] = _LpPandas(mod.__dict__["pd"])
        except Exception:
            pass
    mod.__dict__["open"] = lp_open
    return mod


def engineer_class(mod):
    """The pipeline class an engineer module defines, newest name first."""
    for c in ENGINEER_CLASSES:
        if hasattr(mod, c):
            return getattr(mod, c)
    raise ClassificationError(
        "Axle-classification script defines no known pipeline class.",
        detail="Looked for: " + ", ".join(ENGINEER_CLASSES),
        hint="The class was renamed; add the new name to ENGINEER_CLASSES.")


def detect_log_format(scan_dir_or_csv):
    """
    (unit, triggers_csv_path) - which Host column this scan's trigger log carries.

    Decided ONLY by the presence of the Host_Timestamp_usec column, read from the
    header row. Guessing from the magnitude of the numbers would be wrong on a
    controller that has been up long enough for a millisecond counter to reach the
    microsecond range, and the two pipelines behave very differently on a wrong call.
    """
    path = scan_dir_or_csv
    if lp_isdir(path):
        # lp_find, not glob: the extended-length prefix carries a '?' and glob would
        # read it as a metacharacter. The walk stops at the first hit.
        hits = lp_find(path, "triggers_data.csv", max_hits=1)
        path = hits[0] if hits else None
    if not path or not lp_isfile(path):
        return UNIT_MSEC, None
    try:
        with lp_open(path, "r", encoding="utf-8", errors="replace") as fh:
            header = fh.readline()
    except Exception:
        return UNIT_MSEC, path
    cols = [c.strip() for c in header.split(",")]
    return (UNIT_USEC if USEC_TRIGGER_COL in cols else UNIT_MSEC), path


def pipeline_for(scan_dir):
    """
    (script name, unit) - which engineer pipeline this scan should be classified by.

    The newer absolute-sync pipeline is used exactly when the scan carries the
    microsecond columns it was written against; anything else keeps the millisecond
    pipeline. See the ENGINEER_SCRIPT_SYNC comment for what happens when that choice
    is made the other way round.
    """
    unit, _csv = detect_log_format(scan_dir)
    if unit == UNIT_USEC and find_engineer_script(ENGINEER_SCRIPT_SYNC):
        return ENGINEER_SCRIPT_SYNC, unit
    return ENGINEER_SCRIPT, unit


# ------------------------------------------------------------------
# Result object
# ------------------------------------------------------------------
class AxleIndex:
    """
    In-memory view of the engineer's file_manifest. Holds no file copies - only the
    mapping from an absolute source path to the axle bucket HE assigned it.
    """

    def __init__(self):
        self.scan_dir = None
        self.n_axles = 0
        self.m_pulses = 0
        self.axle_windows = {}
        self.ocr_windows = {}
        self.gate_pulses = {}
        self.assign = {}                       # abs path -> "Axle 1" / "Trash"
        # v15: abs path -> set of FURTHER axle buckets the same file is on (shared
        # frames). Never holds Trash and never repeats the assign[] bucket.
        self.also = {}
        self.meta = {}                         # abs path -> dict(side, media)
        self.warnings = []
        self.classifier = None
        # which engineer pipeline produced this index, and in what time unit its
        # windows and gate pulses are expressed. Everything downstream that needs
        # milliseconds scales by host_per_ms instead of assuming one of the two.
        self.pipeline = None                   # script filename
        self.pipeline_class = None             # class name inside it
        self.time_unit = None                  # UNIT_MSEC / UNIT_USEC
        self.host_per_ms = 1.0
        self.script_path = None
        # WHERE the split came from. SOURCE_SCRIPT = his state machine ran here;
        # SOURCE_TREE = it was read off an existing classified folder tree and
        # nothing was re-derived. Everything the viewer says about a scan's
        # classification is qualified by this, so it is never left to be inferred.
        self.source = SOURCE_SCRIPT
        self.tree_root = None                  # the Axle_N root, when source is TREE
        # The file his manifest CSV was read from, when source is MANIFEST.
        self.manifest_path = None
        # abs path -> the host timestamp HE recorded for that file, in microseconds.
        # Populated only from the manifest, where it is a column he wrote; it is a
        # measurement, not a window, and nothing here turns it into one.
        self.host_ts = {}

    # ---- lookups -------------------------------------------------
    def axle_labels(self):
        """Ordered axle bucket names, e.g. ['Axle 1', 'Axle 2']."""
        return [f"Axle {i}" for i in range(1, self.n_axles + 1)]

    def bucket_of(self, path):
        """The PRIMARY bucket (lowest axle) - see buckets_of() for all of them."""
        return self.assign.get(os.path.abspath(path), TRASH)

    def buckets_of(self, path):
        """Every bucket a file is on, primary first: ['Axle 1', 'Axle 2'] / ['Trash']."""
        p = path if path in self.assign else os.path.abspath(path)
        b = self.assign.get(p)
        if b is None:
            return [TRASH]
        return [b] + sorted(self.also.get(p, ()), key=_bucket_order)

    def pairs(self):
        """
        Every (path, bucket) membership: one per file, plus one more per further axle
        a shared frame is on. Per-axle views iterate THIS, not assign.items(), so a
        frame on Axle 1 and Axle 2 is listed under both.
        """
        also = self.also
        for p, b in self.assign.items():
            yield p, b
            for b2 in also.get(p, ()):
                yield p, b2

    def n_shared(self):
        """Files on more than one axle."""
        return sum(1 for s in self.also.values() if s)

    def add(self, path, bucket):
        """
        Record that `path` is on `bucket`. Returns what it was:
          'new'       first bucket for this file
          'same'      the bucket it is already on (a repeated row)
          'shared'    a further AXLE - kept, the file is now on both
          'conflict'  an axle and Trash for the same file - the axle is kept
        The primary (assign[]) is always the lowest axle, so the result does not
        depend on the order the rows arrive in.
        """
        cur = self.assign.get(path)
        if cur is None:
            self.assign[path] = bucket
            return "new"
        if cur == bucket or bucket in self.also.get(path, ()):
            return "same"
        if cur == TRASH or bucket == TRASH:
            if cur == TRASH:
                self.assign[path] = bucket          # the axle wins over Trash
            return "conflict"
        lo, hi = sorted((cur, bucket), key=_bucket_order)
        self.assign[path] = lo
        extra = self.also.setdefault(path, set())
        extra.discard(lo)
        extra.add(hi)
        return "shared"

    def axle_for_host(self, host):
        """
        Which axle's window contains this Host timestamp, or None.

        Reads the windows his _build_axle_windows() produced, so the trigger table
        answers with the same windows that placed the files.
        """
        if host is None:
            return None
        for i in range(1, self.n_axles + 1):
            for win in self.axle_windows.get(i, {}).values():
                if win[0] <= host <= win[1]:
                    return f"Axle {i}"
            for win in self.ocr_windows.get(i, []):
                if win[0] <= host <= win[1]:
                    return f"Axle {i}"
        return None

    def paths_in(self, bucket, media=None, side=None):
        """
        Every path in a bucket, UNSORTED - for counting and set-building.

        files_for() sorts with _frame_key so a viewer can show frames in order, and
        that sort is real work: the Trash bucket of a 3-axle scan holds 4,424 files
        and the Laser Grooves dropdown counts four buckets on every load. Nothing
        that only needs "how many" or "which set" should pay for it.
        """
        # v15: membership, not the primary bucket - a shared frame is in both axles
        for path, b in self.pairs():
            if b != bucket:
                continue
            if media is not None or side is not None:
                m = self.meta.get(path, {})
                if media is not None and m.get("media") != media:
                    continue
                if side is not None and m.get("side") != side:
                    continue
            yield path

    def files_for(self, bucket, media=None, side=None):
        """Absolute paths in a bucket, optionally narrowed by media type and side."""
        return sorted(self.paths_in(bucket, media, side), key=_frame_key)

    def counts(self):
        """
        {bucket: {media: n}} for the summary line and the coverage warnings.
        v15: a shared frame counts on every axle it is on (the per-axle number is
        what that axle has to work with); n_shared() says how many that is.
        """
        c = defaultdict(lambda: defaultdict(int))
        for p, b in self.pairs():
            c[b][self.meta.get(p, {}).get("media", "?")] += 1
        return {k: dict(v) for k, v in c.items()}


def _bucket_order(bucket):
    """Axle 1 < Axle 2 < ... < Axle 10 < Trash < anything else."""
    m = re.match(r"^Axle (\d+)$", bucket or "")
    if m:
        return (0, int(m.group(1)), "")
    return (1 if bucket == TRASH else 2, 0, bucket or "")


def _frame_key(path):
    """frame_10 sorts after frame_9, and the camera folder keeps files grouped."""
    name = os.path.basename(path)
    m = _FRAME_RE.search(name)
    idx = int(m.group(1)) if m else -1
    return (os.path.basename(os.path.dirname(path)).lower(), idx, name.lower())


# ------------------------------------------------------------------
# The adapter subclass
# ------------------------------------------------------------------
def _make_adapter(base_cls):
    """Build the subclass lazily so the engineer's class is the real base."""

    class _InMemoryClassifier(base_cls):
        """
        Runs his pipeline unchanged and reads the result out of his file_manifest.
        Only execute_copy is replaced, because it writes into the scan folder.
        """

        def build_index(self):
            """Mirror his file_manifest into an AxleIndex. No decisions are made here."""
            index = AxleIndex()
            # main_dir is the EXTENDED-LENGTH folder classify_scan handed him (see
            # the LONG PATHS block), and every path he derived from it carries the
            # prefix. It is stripped here, at the one point where his manifest
            # becomes the viewer's index, so nothing downstream ever has to know the
            # difference and no key can fail to match a path built anywhere else.
            index.scan_dir = unlp(self.main_dir)
            conflicts = 0
            for item in self.file_manifest:
                # his two scripts name the source path differently: the millisecond
                # pipeline writes 'src_file', the absolute-sync one 'original_full_path'
                src = item.get("src_file") or item.get("original_full_path")
                if not src:
                    continue
                p = os.path.abspath(unlp(src))
                axle = item["target_axle"]
                # his labels are 'Axle_1' / 'Trash'; only the separator is changed,
                # so the UI reads "Axle 1" while the grouping stays identical
                # v15: add(), not assign[p] = - his newer classifier lists a shared
                # frame once per axle, and a plain assignment kept only the LAST one
                bucket = TRASH if axle == TRASH else str(axle).replace("_", " ")
                got = index.add(p, bucket)
                if got == "conflict":
                    conflicts += 1
                if got == "new":
                    index.meta[p] = {"side": item["side"],
                                     "media": item["media_type"]}
            note = shared_frames_note(index, conflicts)
            if note:
                index.warnings.append(note)
            return index

        def execute_copy(self):                     # never used by the viewer
            raise NotImplementedError(
                "The viewer classifies in memory; use export_tree() to write files.")

        def process_and_execute(self):
            """
            The absolute-sync pipeline's writer. Replaced for the same reason
            execute_copy is: it writes a manifest CSV into the scan folder, copies
            every classified file, and INVERTS every TIF it copies. None of that may
            happen because a scan was opened for viewing.
            """
            raise NotImplementedError(
                "The viewer classifies in memory; use export_tree() to write files.")

        def plot_and_show_system_diagram(self):
            """
            His diagram is drawn by the viewer's own canvas (see the engineer-diagram
            view in the app), not through pyplot. Left unreachable here because this
            one calls plt.show(), which opens a second, non-Qt window and blocks.
            """
            raise NotImplementedError(
                "The viewer renders his diagram on its own canvas; see "
                "wa_draw_engineer_diagram().")

    return _InMemoryClassifier


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------
def classify_scan(scan_dir, script_path=None, script_name=None):
    """
    Run the engineer's script over `scan_dir` and return an AxleIndex holding
    exactly what his file_manifest says.

    WHICH of his two pipelines runs is decided by the scan's own trigger-log columns
    (pipeline_for), not by a preference: the absolute-sync pipeline's windows are
    microsecond literals and are wrong by a factor of 1000 on a millisecond log. The
    choice is recorded on the index so the viewer can name it.

    Raises ClassificationError with a user-readable message when the scan cannot
    be classified - the caller flags the scan and falls back to per-camera tabs.
    """
    unit = UNIT_MSEC
    if script_name is None and script_path is None:
        script_name, unit = pipeline_for(scan_dir)
    elif script_name is not None:
        unit, _csv = detect_log_format(scan_dir)
    mod = load_engineer_module(script_path, script_name)
    base = engineer_class(mod)
    cls = _make_adapter(base)
    # An ORDINARY path, deliberately: his script globs on main_dir, and the
    # extended-length prefix would be read as a glob metacharacter. Long paths are
    # handled INSIDE his module instead - see install_long_path_shims().
    clf = cls(scan_dir)

    if not lp_isfile(clf.triggers_file):
        raise ClassificationError(
            "This scan has no wheel-assembler trigger log.",
            detail=clf.triggers_file,
            hint="Axle classification needs wheel_assembler/triggers_data.csv. "
                 "Re-download the scan with the wheel_assembler folder included.")

    try:
        clf.parse_triggers()
    except IndexError as exc:
        raise ClassificationError(
            "The gate pulses in this scan are unbalanced, so the axle state "
            "machine cannot resolve the axle count.",
            detail=_gate_detail(clf, unit) + f"\n\nIndexError: {exc}",
            hint="Every gate has to fire once per axle. Open the Wheel Assembler "
                 "tab - the trigger log is complete and every edge is still there; "
                 "it is the per-axle grouping that cannot be derived from it.")
    except Exception as exc:
        raise ClassificationError(
            "The axle state machine failed on this scan.",
            detail=f"{type(exc).__name__}: {exc}",
            hint="See the console for the full traceback.")

    if clf.N < 1:
        raise ClassificationError(
            "No axles could be resolved from the trigger log.",
            detail=_gate_detail(clf, unit),
            hint="The scan holds no complete gate pulse, so nothing can be "
                 "attributed to an axle.")

    try:
        clf.scan_files()                       # HIS file walk, unmodified
    except Exception as exc:
        raise ClassificationError(
            "The axle classification failed while reading this scan's files.",
            detail=f"{type(exc).__name__}: {exc}",
            hint="See the console for the full traceback.")

    idx = clf.build_index()
    idx.n_axles = clf.N
    idx.m_pulses = clf.M
    idx.axle_windows = clf.axle_windows
    idx.ocr_windows = clf.ocr_windows
    idx.gate_pulses = normalise_gate_pulses(clf.gate_pulses_host)
    idx.pipeline = script_name or ENGINEER_SCRIPT
    idx.pipeline_class = getattr(mod, "MM_PIPELINE_CLASS", base.__name__)
    idx.time_unit = unit
    idx.host_per_ms = HOST_PER_MS.get(unit, 1.0)
    idx.script_path = getattr(mod, "__file__", None)
    idx.warnings.extend(_coverage_warnings(idx))
    idx.warnings.extend(_pipeline_warnings(idx, clf))
    idx.warnings.extend(trigger_cross_check(idx, clf.triggers_file))
    _lp_note = long_path_warning(idx.scan_dir)
    if _lp_note:
        idx.warnings.append(_lp_note)
    idx.classifier = clf
    return idx


def normalise_gate_pulses(gate_pulses):
    """
    Add rise_host / fall_host to every pulse, whatever the source script called them.

    The millisecond pipeline writes rise_host / fall_host; the absolute-sync one
    writes rise_host_usec / fall_host_usec. Everything downstream - the per-axle plot,
    the trigger table's Axle column, the vehicle map - reads one pair of names. The
    original keys are kept, so nothing his script wrote is lost or renamed.
    """
    out = {}
    for gate, pulses in (gate_pulses or {}).items():
        norm = []
        for p in pulses or []:
            q = dict(p)
            if q.get("rise_host") is None:
                q["rise_host"] = p.get("rise_host_usec")
            if q.get("fall_host") is None:
                q["fall_host"] = p.get("fall_host_usec")
            norm.append(q)
        out[gate] = norm
    return out


def _pipeline_warnings(idx, clf):
    """
    Say what the chosen pipeline could not reach on THIS scan.

    Not a health check on his script - a statement of what the index in front of the
    user does and does not cover, so a half-empty vehicle map is never mistaken for a
    scan with nothing in it.
    """
    warns = []
    n_declared = int(getattr(clf, "N", 0) or 0)
    n_windowed = len([a for a in range(1, n_declared + 1)
                      if (idx.axle_windows or {}).get(a)])
    if n_declared > n_windowed:
        warns.append(
            "%s resolved %d axles but builds slicing windows for only %d - axles %s "
            "can match no laser or tread file."
            % (idx.pipeline_class, n_declared, n_windowed,
               ", ".join(str(a) for a in range(n_windowed + 1, n_declared + 1))))
    # A high trash ratio is only DIAGNOSTIC under the absolute-sync pipeline, where it
    # is the signature of its microsecond windows meeting a millisecond log. Under the
    # millisecond pipeline a large Trash bucket is ordinary and already reported by
    # _coverage_warnings, so repeating it here with a unit hint would just mislead.
    if idx.pipeline == ENGINEER_SCRIPT_SYNC:
        counts = idx.counts()
        total = sum(sum(v.values()) for v in counts.values())
        trashed = sum(counts.get(TRASH, {}).values())
        if total and trashed / float(total) > 0.5:
            warns.append(
                "%d of %d classified files (%.0f%%) matched no axle window under the "
                "microsecond pipeline. Check that this scan's trigger log and groove "
                "CSVs really carry the usec columns."
                % (trashed, total, 100.0 * trashed / total))
        has_lmi = lp_has_dir(clf.main_dir, "LMI_*")
        if has_lmi and not any(MEDIA_LASER in v for v in counts.values()):
            warns.append(
                "No laser file was classified at all. The absolute-sync pipeline reads "
                "a groove CSV only when it carries Device_Timestamp_usec, "
                "Host_Timestamp_usec or Host; a log written with timestamp_msec "
                "matches none of them and is skipped silently.")
    return warns


def _gate_detail(clf, unit=None):
    """
    Every gate's pulses, with widths, and what makes them unbalanced.

    This is the text on the red banner when the state machine cannot resolve a scan,
    and on a test day it is the only thing standing between "the tool broke" and "the
    trigger wiring did something". His script takes M from the BUSIEST gate and then
    indexes every gate to M, so one gate with fewer complete pulses than another is
    an IndexError - the count per gate is therefore the diagnosis, not a detail.
    Widths come with it because an abnormally wide pulse is the usual reason a gate
    lost one: the sensor stayed made across two axles instead of releasing between
    them.
    """
    pulses = getattr(clf, "gate_pulses_host", None) or {}
    if not pulses:
        return "Gate pulses found:\n   (none)"
    # HIS units, handed in by the caller. The host counter is microseconds on the
    # current firmware and milliseconds on the older logs; detect_log_format has
    # already decided which, and guessing it again from an attribute on his class
    # gets it wrong by a factor of 1000 - it printed 285 s for a 0.29 s pulse.
    per_s = 1e6 if unit == UNIT_USEC else 1e3
    parts = []
    counts = {}
    for gate in sorted(pulses):
        rows = pulses[gate] or []
        counts[gate] = len(rows)
        widths = []
        for pulse in rows:
            rise = pulse.get("rise_host", pulse.get("rise_host_usec"))
            fall = pulse.get("fall_host", pulse.get("fall_host_usec"))
            if rise is None or fall is None:
                continue
            widths.append((fall - rise) / per_s)
        span = ("  widths " + ", ".join("%.2f s" % w for w in widths)) if widths else ""
        parts.append("   %-7s %d complete pulse(s)%s" % (gate + ":", len(rows), span))
    out = "Gate pulses found:\n" + "\n".join(parts)
    if counts and len(set(counts.values())) > 1:
        most = max(counts, key=lambda g: counts[g])
        short = [g for g in counts if counts[g] < counts[most]]
        out += ("\n\nThe gates disagree on how many axles went through: %s fired %d "
                "time(s), %s. The axle state machine indexes every gate to the "
                "busiest one, so it runs off the end of the short one and no "
                "split can be produced.\n\nA gate pulse much wider than the others "
                "is the usual cause - the sensor stayed made across two axles instead "
                "of releasing between them. The trigger log itself is complete and "
                "the Wheel Assembler tab still shows every edge; only the per-axle "
                "grouping is unavailable."
                % (most, counts[most],
                   ", ".join("%s %d" % (g, counts[g]) for g in sorted(short))))
    return out


def _coverage_warnings(idx):
    """
    Surface the coverage holes rather than showing a half-empty axle as if it were
    complete.

    Worded from the SOURCE, because the same empty bucket means three different
    things: a classification his script ran means nothing matched that axle's window;
    a folder tree means the files are not in the folder; a manifest means he recorded
    nothing on that axle when the scan was processed. Reporting any of them as
    another would send someone hunting a bug that does not exist.
    """
    warns = []
    counts = idx.counts()
    why = {SOURCE_TREE: "is in this axle's folders",
           SOURCE_MANIFEST: "is on this axle in the manifest",
           }.get(getattr(idx, "source", SOURCE_SCRIPT), "matched its window")
    for axle in idx.axle_labels():
        got = counts.get(axle, {})
        missing = [m for m in (MEDIA_LASER, MEDIA_TREAD, MEDIA_OCR) if not got.get(m)]
        if missing:
            warns.append(f"{axle}: no {', '.join(missing)} data {why}.")
    trash = counts.get(TRASH, {})
    if trash:
        detail = ", ".join(f"{n} {m}" for m, n in sorted(trash.items()))
        warns.append(f"Unassigned (Trash): {sum(trash.values())} files - {detail}.")
    return warns


# ------------------------------------------------------------------
# Reading an already-classified tree
# ------------------------------------------------------------------
def _axle_dirs(folder):
    """(number, path) for every Axle_N directory directly inside `folder`."""
    out = []
    try:
        entries = sorted(lp_listdir(folder))
    except OSError:
        return out
    for name in entries:
        m = AXLE_DIR_RE.match(name)
        if not m:
            continue
        path = os.path.join(folder, name)
        if lp_isdir(path):
            try:
                out.append((int(m.group(1)), path))
            except ValueError:
                continue
    return sorted(out)


def detect_classified_tree(root, max_depth=TREE_SEARCH_DEPTH):
    """
    The directory holding this scan's Axle_N buckets, or None.

    `root` itself is checked first, then his Scan_Output_Classified/, then the other
    immediate children - so the user can open the export, the scan folder that holds
    his output, or the output folder itself, and get the same answer.

    A directory qualifies only when it holds at least one Axle_N subdirectory. That
    single rule is what separates "this is a classification" from "this scan happens
    to have a folder with a number in it", and it is deliberately not loosened to
    Trash/ or Unclassified/ alone: either of those without an axle bucket is not a
    split anything can be read out of.
    """
    if not root or not lp_isdir(root):
        return None
    if _axle_dirs(root):
        return root
    if max_depth < 1:
        return None
    # his writer's folder first, by name, so it wins over an alphabetical neighbour
    candidates = []
    engineer_out = os.path.join(root, ENGINEER_OUTPUT_DIR)
    if lp_isdir(engineer_out):
        candidates.append(engineer_out)
    try:
        for name in sorted(lp_listdir(root)):
            path = os.path.join(root, name)
            if lp_isdir(path) and path not in candidates:
                candidates.append(path)
    except OSError:
        pass
    for path in candidates:
        found = detect_classified_tree(path, max_depth - 1)
        if found:
            return found
    return None


# Camera-folder name fragments that state a side. Only these decide a side when the
# tree carries no side folder (Trash does not, in either layout) - anything else stays
# unknown rather than being guessed. His own rule is "Left if '_l_' in the folder else
# Right", which would file LMI_2_REAR_LEFT as Right; that is fine inside his own run,
# where the folder list is fixed, and not fine here, where the folder came off a disk
# this code did not write.
# Matched against a WHOLE underscore/dash-separated element, never as a substring:
# "LMI_2_REAR_LEFT" contains the letters of both sides ("_rear" starts with r), and a
# substring test files it as ambiguous or, worse, as Right.
SIDE_PATTERNS = ((re.compile(r"(?:^|[_\-])(?:l|left)(?:[_\-]|$)", re.I), "Left"),
                 (re.compile(r"(?:^|[_\-])(?:r|right)(?:[_\-]|$)", re.I), "Right"))


def side_from_name(name):
    """'Left' / 'Right' when the folder name says so unambiguously, else None."""
    low = name or ""
    hits = {side for rx, side in SIDE_PATTERNS if rx.search(low)}
    return hits.pop() if len(hits) == 1 else None


def tree_entry(rel_parts):
    """
    (side, media, folder) for a path relative to an Axle_N or Trash directory.

    Reads the two layouts with one rule: an element that NAMES a side is the side, an
    element that names a media type is the media, and whatever is left immediately
    above the file is the camera folder. Nothing is guessed from the filename - an
    element that names neither leaves that field unknown and is treated as the
    camera folder, which is exactly what his Trash layout writes.
    """
    side = media = None
    rest = list(rel_parts)
    if rest and rest[0].lower() in TREE_SIDES:
        side = rest.pop(0).capitalize()
    if rest and rest[0].lower() in TREE_MEDIA:
        media = TREE_MEDIA[rest.pop(0).lower()]
    folder = rest[-1] if rest else None
    return side, media, folder


def _walk_files(folder):
    """Every file under `folder`, as (abs path, parts relative to it)."""
    out = []
    for root, _dirs, files in lp_walk(folder):
        try:
            rel = os.path.relpath(root, folder)
        except ValueError:                      # different drive: not our tree
            continue
        parts = [] if rel in (".", "") else rel.split(os.sep)
        for name in files:
            out.append((os.path.join(root, name), parts))
    return out


def index_from_tree(tree_root, scan_dir=None):
    """
    Build an AxleIndex from an existing classified folder tree. Nothing is executed,
    no trigger log is read, and no file is moved or written.

    The tree IS the classification: a file's axle, side and media come from where it
    already sits. Files under Unclassified/ are left out of the index for the same
    reason they are left out of a freshly classified scan - they were never attributed
    to an axle - and they are still shown by the viewer's per-camera tabs.

    Raises ClassificationError when the tree holds no axle bucket at all, so the
    caller can fall back exactly as it does for a classification that could not run.
    """
    axles = _axle_dirs(tree_root)
    if not axles:
        raise ClassificationError(
            "This folder is not a classified axle tree.",
            detail=tree_root,
            hint="A classified tree holds Axle_1, Axle_2 ... directories at its top "
                 "level.")

    idx = AxleIndex()
    idx.scan_dir = scan_dir or tree_root
    idx.source = SOURCE_TREE
    idx.tree_root = tree_root
    idx.n_axles = max(n for n, _p in axles)

    n_files = 0
    for number, path in axles:
        bucket = f"Axle {number}"
        for src, parts in _walk_files(path):
            side, media, folder = tree_entry(parts)
            side = side or side_from_name(folder)
            idx.assign[os.path.abspath(src)] = bucket
            idx.meta[os.path.abspath(src)] = {"side": side or "unknown",
                                              "media": media or "unknown",
                                              "folder": folder}
            n_files += 1

    trash_dir = os.path.join(tree_root, TRASH)
    if lp_isdir(trash_dir):
        for src, parts in _walk_files(trash_dir):
            _side, media, folder = tree_entry(parts)
            # Neither layout writes a side folder under Trash, so the camera folder
            # name is the only thing left that can carry one. Unknown when it does
            # not - a Trash frame with no side is still shown, just not sided.
            _side = _side or side_from_name(folder)
            idx.assign[os.path.abspath(src)] = TRASH
            idx.meta[os.path.abspath(src)] = {"side": _side or "unknown",
                                              "media": media or "unknown",
                                              "folder": folder}
            n_files += 1

    if not n_files:
        raise ClassificationError(
            "This classified tree holds no files.",
            detail=tree_root,
            hint="The Axle folders exist but are empty - re-export or re-download "
                 "the classified scan.")

    # Gaps in the axle numbering are a real finding, not a display detail: Axle_1 and
    # Axle_3 with no Axle_2 means a bucket was lost between the split and here.
    present = {n for n, _p in axles}
    missing = [n for n in range(1, idx.n_axles + 1) if n not in present]
    if missing:
        idx.warnings.append(
            "the axle numbering runs to %d but %s absent from this tree - a bucket "
            "was lost between the split and this folder."
            % (idx.n_axles,
               ", ".join("Axle %d" % n for n in missing)
               + (" is" if len(missing) == 1 else " are")))
    idx.warnings.extend(_coverage_warnings(idx))
    idx.warnings.extend(trigger_cross_check(idx))
    idx.warnings.append(
        "read from the folder tree as it stands - the split script was not re-run, "
        "so there are no gate windows and the trigger table's Axle column stays "
        "empty.")
    # His writer INVERTS every TIF it copies, so frames from his output folder are not
    # the pixels the camera produced. Stated, never corrected: re-inverting here would
    # mean the viewer showing something different from the file on disk.
    if os.path.basename(os.path.normpath(tree_root)).lower() \
            == ENGINEER_OUTPUT_DIR.lower():
        idx.warnings.append(
            "this tree was written by the engineer's own classifier, which INVERTS "
            "every TIF it copies - frames here are not the camera's original pixels.")
    return idx


# ------------------------------------------------------------------
# Reading his manifest CSV
# ------------------------------------------------------------------
def find_classification_manifest(scan_dir):
    """
    His manifest CSV for this scan, or None.

    Looked for in <scan>/Scan_Output_Classified/ and - so that pointing the viewer
    straight at the output folder works too - in the folder itself. Nothing deeper is
    searched: a manifest that is not one of those two is not this scan's record.
    """
    if not scan_dir:
        return None
    for folder in (os.path.join(scan_dir, ENGINEER_OUTPUT_DIR), scan_dir):
        path = os.path.join(folder, MANIFEST_NAME)
        if lp_isfile(path):
            return path
    return None


def find_system_diagram(scan_dir):
    """His saved system-pulse PNG for this scan, or None. Same two places."""
    if not scan_dir:
        return None
    for folder in (os.path.join(scan_dir, ENGINEER_OUTPUT_DIR), scan_dir):
        path = os.path.join(folder, ENGINEER_DIAGRAM_NAME)
        if lp_isfile(path):
            return path
    return None


def _manifest_axle_label(raw):
    """
    ('Axle 3', 3) / (TRASH, 0) / (None, 0) for one target_axle cell.

    Only the separator changes, exactly as build_index() does it, so the buckets a
    manifest produces and the buckets a live run produces are the same strings. A
    cell that names neither an axle nor Trash is REJECTED rather than guessed - a
    silently mis-parsed label would put files on an axle nobody assigned them to.
    """
    txt = (raw or "").strip()
    if not txt:
        return None, 0
    if txt.lower() == TRASH.lower():
        return TRASH, 0
    m = MANIFEST_AXLE_RE.match(txt)
    if not m:
        return None, 0
    try:
        n = int(m.group(1))
    except ValueError:
        return None, 0
    return (f"Axle {n}", n) if n >= 1 else (None, 0)


def _manifest_rows(csv_path):
    """(rows, fieldnames) from his CSV. utf-8-sig so a BOM is not read into a name."""
    with lp_open(csv_path, "r", encoding="utf-8-sig", errors="replace",
                 newline="") as fh:
        rd = csv.DictReader(fh)
        return list(rd), [(c or "").strip() for c in (rd.fieldnames or [])]


def _manifest_local_path(row, scan_dir, root=None):
    """
    The file on THIS machine for one manifest row, or None.

    `original_full_path` is his PT path (/mnt/data/sessions/...) and is never opened;
    the relative path is what carries over. A row whose relative path escapes the
    scan folder is dropped rather than followed - a manifest is data, and a '..' in
    it must not reach outside the scan the user opened.

    `root` is the caller's already-absolute scan folder. Passing it in turns two
    os.path.abspath() calls per row into none: abspath goes to the OS
    (nt._getfullpathname) every time, and at 5,364 rows that was most of the cost of
    reading a manifest. normpath does the same collapsing in pure Python, so the
    '..' guard below is unchanged - it is the collapsing that makes an escape
    visible, not the syscall.
    """
    rel = (row.get("original_relative_path") or "").strip()
    if not rel:
        return None
    rel = rel.replace("\\", os.sep).replace("/", os.sep).strip(os.sep)
    if not rel:
        return None
    if root is None:
        root = os.path.abspath(scan_dir)
    full = os.path.normpath(os.path.join(root, rel))
    if not os.path.normcase(full).startswith(os.path.normcase(root) + os.sep):
        return None
    return full


class _DirCache:
    """
    "Is this file there?" answered from ONE listing per directory.

    A manifest names a few thousand files spread over about ten camera folders, and
    asking the filesystem about each one separately is a few thousand syscalls to
    learn what ten listings already say. Measured on the 3-axle scan of 2026-09-07
    (5,364 rows, 10 folders): 0.54 s -> 0.16 s for the whole read.

    Case-folded, because the manifest is written on Linux and read on Windows, where
    the same file can be spelled either way.
    """

    def __init__(self):
        self._dirs = {}

    def _names(self, folder):
        key = os.path.normcase(folder)
        got = self._dirs.get(key)
        if got is None:
            try:
                got = {os.path.normcase(n) for n in lp_listdir(folder)}
            except OSError:
                got = set()
            self._dirs[key] = got
        return got

    def isfile(self, path):
        folder, name = os.path.split(path)
        if not name:
            return False
        return os.path.normcase(name) in self._names(folder)

    def listing(self, folder):
        """The folder's entry names, as they are on disk."""
        try:
            return lp_listdir(folder)
        except OSError:
            return []


def _manifest_host_ts(row):
    """His calculated host timestamp for a row, in usec, or None when unreadable."""
    raw = (row.get("calculated_host_timestamp_usec") or "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def shared_frames_note(idx, conflicts=0):
    """
    v15: one line on shared frames, or None. Sharing is his classifier's intent (two
    axles' windows overlap), so it is stated as a fact, not as a fault; a file put
    on an axle AND in Trash is the only real contradiction and is worded as one.
    """
    bits = []
    n = idx.n_shared()
    if n:
        combos = defaultdict(int)
        for p, extra in idx.also.items():
            if extra:
                combos[" + ".join(idx.buckets_of(p))] += 1
        bits.append("%d file(s) are shared by more than one axle (%s) and are listed "
                    "under each of them." % (n, ", ".join(
                        "%s: %d" % kv for kv in sorted(combos.items()))))
    if conflicts:
        bits.append("%d file(s) are on an axle AND in Trash - shown on the axle; the "
                    "classifier output contradicts itself there." % conflicts)
    return "  ".join(bits) or None


def index_from_manifest(csv_path, scan_dir=None):
    """
    Build an AxleIndex from his Scan_Output_Classified manifest CSV.

    Nothing is executed, no trigger log is parsed and no file is written. The CSV IS
    the classification: a file's axle, side and media are the ones he recorded on the
    run that produced this scan.

    What is checked, and reported rather than silently absorbed:
      * a row whose file is not on this disk is skipped and counted.
      * a row whose target_axle names neither an axle nor Trash is skipped and
        counted - never defaulted to Trash, which would read as "he binned it".
      * two rows for the same file that disagree are counted; the FIRST wins, so the
        result does not depend on row order changing between exports.
      * files sitting in a folder the manifest covers but carrying no row of their
        own are counted, so a truncated manifest cannot look like a complete one.

    Raises ClassificationError when the CSV cannot be read, is missing a column the
    split depends on, or yields no usable row - the caller then falls back exactly as
    it does for a classification that could not run.
    """
    csv_path = os.path.abspath(csv_path)
    if not scan_dir:
        # <scan>/Scan_Output_Classified/<csv>  ->  <scan>
        parent = os.path.dirname(csv_path)
        scan_dir = (os.path.dirname(parent)
                    if os.path.basename(parent).lower() == ENGINEER_OUTPUT_DIR.lower()
                    else parent)
    if not lp_isfile(csv_path):
        raise ClassificationError(
            "This scan has no classification manifest.",
            detail=csv_path,
            hint="A complete scan carries %s/%s."
                 % (ENGINEER_OUTPUT_DIR, MANIFEST_NAME))
    try:
        rows, cols = _manifest_rows(csv_path)
    except Exception as exc:
        raise ClassificationError(
            "The classification manifest could not be read.",
            detail="%s\n\n%s: %s" % (csv_path, type(exc).__name__, exc),
            hint="Re-download the scan, or remove %s to fall back to classifying "
                 "here." % ENGINEER_OUTPUT_DIR)
    missing_cols = [c for c in MANIFEST_REQUIRED_COLS if c not in cols]
    if missing_cols:
        raise ClassificationError(
            "The classification manifest is missing a column the split depends on.",
            detail="%s\n\nmissing: %s\nfound: %s"
                   % (csv_path, ", ".join(missing_cols), ", ".join(cols) or "(none)"),
            hint="This manifest was written by a different version of his "
                 "classifier. The scan can still be classified here instead.")

    idx = AxleIndex()
    idx.scan_dir = scan_dir
    idx.source = SOURCE_MANIFEST
    idx.manifest_path = csv_path
    idx.tree_root = os.path.dirname(csv_path)
    # His manifest timestamps are microseconds, whatever pipeline wrote them: the
    # column is named for its unit. Nothing here re-derives a window from them.
    idx.time_unit = UNIT_USEC
    idx.host_per_ms = HOST_PER_MS[UNIT_USEC]

    n_axles = 0
    absent = bad_axle = no_path = dupes = 0
    root = os.path.abspath(scan_dir)
    seen_dirs = _DirCache()
    for row in rows:
        bucket, number = _manifest_axle_label(row.get("target_axle"))
        if bucket is None:
            bad_axle += 1
            continue
        path = _manifest_local_path(row, scan_dir, root)
        if path is None:
            no_path += 1
            continue
        if not seen_dirs.isfile(path):
            absent += 1
            continue
        # v15: a second row for the same file is a SHARED frame (his classifier
        # lists it once per axle) - kept on both. Only axle-vs-Trash is a conflict.
        got = idx.add(path, bucket)
        if got == "conflict":
            dupes += 1
        if got != "new":
            n_axles = max(n_axles, number)
            continue
        idx.meta[path] = {"side": (row.get("side") or "").strip() or "unknown",
                          "media": (row.get("media_type") or "").strip() or "unknown",
                          "folder": ((row.get("original_subfolder") or "").strip()
                                     or os.path.basename(os.path.dirname(path)))}
        ts = _manifest_host_ts(row)
        if ts is not None:
            idx.host_ts[path] = ts
        n_axles = max(n_axles, number)

    if not idx.assign:
        raise ClassificationError(
            "The classification manifest holds no usable row.",
            detail="%s\n\n%d row(s) read; %d named no axle, %d had no usable path, "
                   "%d name a file that is not in this folder."
                   % (csv_path, len(rows), bad_axle, no_path, absent),
            hint="The manifest does not describe the files in this scan folder. "
                 "Classifying here instead will use the trigger log.")
    idx.n_axles = n_axles

    if absent:
        idx.warnings.append(
            "%d file(s) in the manifest are not in this scan folder and are not "
            "shown - the download is incomplete, or the manifest is from another "
            "scan." % absent)
    if no_path:
        idx.warnings.append(
            "%d manifest row(s) carry no usable relative path and were skipped."
            % no_path)
    if bad_axle:
        idx.warnings.append(
            "%d manifest row(s) name neither an axle nor Trash and were skipped "
            "rather than binned." % bad_axle)
    note = shared_frames_note(idx, dupes)
    if note:
        idx.warnings.append(note)
    # Gaps in the numbering are the same finding here as in a tree: a bucket that
    # exists nowhere between Axle 1 and the highest axle means one was lost.
    present = {_manifest_axle_label(r.get("target_axle"))[1] for r in rows}
    gaps = [n for n in range(1, n_axles + 1) if n not in present]
    if gaps:
        idx.warnings.append(
            "the axle numbering runs to %d but %s absent from the manifest."
            % (n_axles, ", ".join("Axle %d" % n for n in gaps)
               + (" is" if len(gaps) == 1 else " are")))
    unlisted = _manifest_unlisted(idx, seen_dirs)
    if unlisted:
        idx.warnings.append(
            "%d file(s) sit in folders this manifest covers but have no row of their "
            "own; they are not attributed to any axle." % unlisted)
    idx.warnings.extend(_coverage_warnings(idx))
    # The manifest carries no windows, but the SCAN carries the trigger log they were
    # derived from - so the split can still be held up against what the controller
    # actually did. Report only; nothing here re-buckets a file.
    idx.warnings.extend(trigger_cross_check(idx))
    _lp_note = long_path_warning(idx.scan_dir)
    if _lp_note:
        idx.warnings.append(_lp_note)
    idx.warnings.append(
        "read from %s as his classifier wrote it - the split script was not re-run, "
        "so there are no gate windows and the trigger table's Axle column stays "
        "empty." % MANIFEST_NAME)
    return idx


# ------------------------------------------------------------------
# CROSS-CHECK AGAINST THE TRIGGER LOG, 2026-09-08   (REPORT ONLY)
# ------------------------------------------------------------------
# A split is either his manifest, his state machine, or a folder tree, and in all
# three cases the viewer takes it as final. What the viewer CAN do without touching
# it is read the trigger log - 80 rows, the controller's own record of what fired -
# and say where the two do not line up.
#
# Everything below is a STATEMENT, never a correction. No file changes bucket, no
# count is adjusted and no window is re-derived. The reason is the same one the rest
# of this module is written on: the split is the record of how the scan was
# processed, and a viewer that quietly disagreed with it would be showing a split
# nothing was processed with.
#
# WHAT IT CATCHES, measured on 2026-09-07T14-09-47 (3 axles):
#   * the log carries three complete gate1 pulses and his manifest puts the third
#     axle's 8 OCR frames in Trash, so Axle 3 has no OCR at all.
#   * the log carries three complete gate3 pulses and his manifest has no laser file
#     on Axle 3 and none inside gate3 windows 2 or 3.
#   * 4,424 of 5,364 files (82%) are unassigned.
# None of that is visible from the split on its own: an axle with no laser reads
# exactly like an axle whose laser genuinely did not fire.
GATE_EVENTS = ("gate1", "gate2", "gate3")

# Which medium each gate is supposed to produce, in his model of the firmware:
# gate1 fires the OCR strobes, gate2 opens the tread exposure, gate3 enables the
# laser. Used only to word the warning; nothing is classified from it.
GATE_MEDIA = {"gate1": MEDIA_OCR, "gate2": MEDIA_TREAD, "gate3": MEDIA_LASER}

# His own pads, in microseconds, so the window this check tests a timestamp against
# is the same window his absolute-sync pipeline slices on:
#   PAD_OCR_USEC = 100000        +/- 100 ms around the gate-1 edge
#   CAM_WINDOW_USEC = 2500000    2.5 s of tread exposure from the gate-2 rise
PAD_OCR_USEC = 100000
CAM_WINDOW_USEC = 2500000

# Above this share of unassigned files something is wrong with the split, whatever
# produced it. Not a threshold anything is decided on - the number is always printed.
TRASH_WARN_RATIO = 0.5


def read_gate_pulses(triggers_csv):
    """
    ({gate: [(rise, fall), ...]}, t0) straight from the trigger log, in Host units.

    `t0` is the smallest Host value in the file - the scan's own zero, so every time
    this module prints is on the same axis as the system diagram's.

    A rise is paired with the NEXT fall of the same gate; a rise with no fall after
    it is an incomplete pulse and is dropped, which is the same reading his state
    machine takes. Returns {} for anything unreadable - this is a report, and a
    report that cannot be produced is simply not produced.
    """
    out = {g: [] for g in GATE_EVENTS}
    t0 = None
    if not lp_isfile(triggers_csv):
        return {}, None
    try:
        with lp_open(triggers_csv, "r", encoding="utf-8", errors="replace",
                     newline="") as fh:
            rd = csv.DictReader(fh)
            cols = [(c or "").strip() for c in (rd.fieldnames or [])]
            host_col = (USEC_TRIGGER_COL if USEC_TRIGGER_COL in cols
                        else "Host" if "Host" in cols else None)
            if not host_col:
                return {}, None
            pending = {}
            for row in rd:
                ev = (row.get("Event") or "").strip()
                try:
                    host = int(float(row.get(host_col)))
                except (TypeError, ValueError):
                    continue
                # the scan's own zero, so every time this check prints is the same
                # "seconds from vehicle entry" the system diagram's x axis shows
                if t0 is None or host < t0:
                    t0 = host
                if ev not in out:
                    continue
                rising = str(row.get("Rising_Edge", "")).strip()
                if rising == "1":
                    pending[ev] = host
                elif rising == "0" and ev in pending:
                    out[ev].append((pending.pop(ev), host))
    except Exception:
        return {}, None
    return out, t0


def gate_windows(pulses, gate, index):
    """
    (lo, hi) in Host units for one gate's Nth pulse, using his own pads, or None.

    His model, restated here and nowhere else: OCR is the gate-1 pulse widened by
    PAD_OCR_USEC at both ends, tread is CAM_WINDOW_USEC from the gate-2 rise, laser
    is the gate-3 pulse as it stands.
    """
    try:
        rise, fall = pulses[gate][index]
    except (KeyError, IndexError, TypeError):
        return None
    if gate == "gate1":
        return rise - PAD_OCR_USEC, fall + PAD_OCR_USEC
    if gate == "gate2":
        return rise, rise + CAM_WINDOW_USEC
    return rise, fall


def trigger_cross_check(idx, triggers_csv=None):
    """
    Warnings from comparing an AxleIndex against the trigger log. Report only.

    Cheap by construction: the trigger log is ~80 rows and the index is already in
    memory, so this adds milliseconds to a load and never touches a frame.
    """
    warns = []
    if idx is None:
        return warns
    scan_dir = getattr(idx, "scan_dir", None)
    if not triggers_csv:
        if not scan_dir:
            return warns
        hits = lp_find(scan_dir, "triggers_data.csv", max_hits=1)
        triggers_csv = hits[0] if hits else None
    if not triggers_csv:
        return warns
    pulses, t0 = read_gate_pulses(triggers_csv)
    if not pulses:
        return warns

    counts = idx.counts()
    n_split = int(getattr(idx, "n_axles", 0) or 0)
    n_gate = min(len(pulses.get(g, [])) for g in GATE_EVENTS)
    detail = ", ".join("%s x%d" % (g, len(pulses.get(g, []))) for g in GATE_EVENTS)

    # 1. the log and the split do not agree on how many axles went through
    if n_gate > n_split:
        warns.append(
            "the trigger log holds %d complete gate set(s) (%s) but the split "
            "resolved %d axle(s) - %d axle(s) worth of triggers are attributed to "
            "nothing." % (n_gate, detail, n_split, n_gate - n_split))
    elif n_split > n_gate:
        warns.append(
            "the split resolved %d axle(s) but the trigger log holds only %d "
            "complete gate set(s) (%s) - the extra axle(s) have no gate window."
            % (n_split, n_gate, detail))

    # 2. a gate fired for an axle, and that axle carries nothing of what it produces
    for k in range(min(n_split, n_gate)):
        label = "Axle %d" % (k + 1)
        got = counts.get(label, {})
        for gate in GATE_EVENTS:
            media = GATE_MEDIA[gate]
            if len(pulses.get(gate, [])) > k and not got.get(media):
                warns.append(
                    "%s: %s pulse %d is in the trigger log but no %s file is on this "
                    "axle - the trigger fired and nothing was attributed to it."
                    % (label, gate, k + 1, media))

    # 3. a file on axle N whose own timestamp falls in a DIFFERENT axle's window.
    #    Only where the timestamps are his and are microseconds: they come off the
    #    manifest, where the column is named for its unit. Nothing is re-bucketed -
    #    the count is the finding.
    host_ts = getattr(idx, "host_ts", None) or {}
    if host_ts and getattr(idx, "time_unit", None) == UNIT_USEC:
        misplaced = 0
        outside = 0
        for path, ts in host_ts.items():
            bucket = idx.assign.get(path)
            if not bucket or bucket == TRASH:
                continue
            # v15: a shared frame is in place when it sits in ANY of its axles'
            # windows - judging it by its primary axle alone would call every
            # overlap frame misplaced
            mine = []
            for b in idx.buckets_of(path):
                try:
                    mine.append(int(str(b).split()[-1]) - 1)
                except (ValueError, IndexError):
                    continue
            if not mine:
                continue
            media = (idx.meta.get(path, {}) or {}).get("media")
            gate = next((g for g, m in GATE_MEDIA.items() if m == media), None)
            if gate is None:
                continue
            if any((w and w[0] <= ts <= w[1])
                   for w in (gate_windows(pulses, gate, k) for k in mine)):
                continue
            hit = None
            for other in range(len(pulses.get(gate, []))):
                w = gate_windows(pulses, gate, other)
                if w and w[0] <= ts <= w[1]:
                    hit = other + 1
                    break
            if hit is not None:
                misplaced += 1
            else:
                outside += 1
        if misplaced:
            warns.append(
                "%d file(s) are on one axle but their own timestamp falls inside a "
                "DIFFERENT axle's gate window. The split is shown as it was written; "
                "this is a disagreement inside it, not a correction." % misplaced)
        if outside:
            warns.append(
                "%d file(s) are attributed to an axle although their own timestamp "
                "falls in no gate window at all." % outside)

    # 4. a whole MEDIA BLOCK on an axle that sits outside that axle's own gate.
    #
    # This is the one that matters, and it is the one nothing else shows. On
    # 2026-09-07T14-09-47 his manifest puts 314 Tread frames on Axle 3 whose
    # timestamps run 0.90-3.38 s from vehicle entry, while axle 3's gate-2 window is
    # 21.64-24.14 s: the bucket is labelled Axle 3 and holds the frames from the
    # START of the scan. Per-file counting reports that as "356 files outside a
    # window", which reads like scatter; saying the block's span and the window's
    # span next to each other is what makes it recognisable.
    #
    # Stated, never corrected - the same rule as everything else here.
    if host_ts and t0 is not None and getattr(idx, "time_unit", None) == UNIT_USEC:
        for k in range(min(n_split, max(len(v) for v in pulses.values()) or 0)):
            label = "Axle %d" % (k + 1)
            for gate, media in sorted(GATE_MEDIA.items()):
                ts = [host_ts[p] for p, b in idx.pairs()
                      if b == label and p in host_ts
                      and (idx.meta.get(p, {}) or {}).get("media") == media]
                if not ts:
                    continue
                win = gate_windows(pulses, gate, k)
                if not win:
                    continue
                lo, hi = min(ts), max(ts)
                if lo >= win[0] and hi <= win[1]:
                    continue                    # the block is where it should be
                if lo <= win[1] and hi >= win[0]:
                    continue                    # it overlaps; per-file count has it
                elsewhere = ""
                for other in range(len(pulses.get(gate, []))):
                    w = gate_windows(pulses, gate, other)
                    if w and w[0] <= (lo + hi) // 2 <= w[1]:
                        elsewhere = " - that is axle %d's window" % (other + 1)
                        break
                warns.append(
                    "%s: its %d %s frame(s) run %.2f-%.2f s from vehicle entry, but "
                    "this axle's %s window is %.2f-%.2f s. The whole block is outside "
                    "its own window%s."
                    % (label, len(ts), media, (lo - t0) / 1e6, (hi - t0) / 1e6,
                       gate, (win[0] - t0) / 1e6, (win[1] - t0) / 1e6, elsewhere))

    # 5. how much of the scan the split reached
    total = sum(sum(v.values()) for v in counts.values())
    trashed = sum(counts.get(TRASH, {}).values())
    if total and trashed / float(total) > TRASH_WARN_RATIO:
        warns.append(
            "%d of %d files (%.0f%%) are unassigned - most of this scan is not on "
            "any axle." % (trashed, total, 100.0 * trashed / total))
    return warns


def long_path_warning(scan_dir, limit=260):
    """
    One line when this scan holds files past MAX_PATH, or None.

    They are READABLE - see the LONG PATHS block - so this is not a failure. It is
    worth saying because every other tool on the machine (Explorer's copy, a plain
    python script, 7-Zip) still fails on them, and because a shorter download folder
    makes the problem go away for all of them.
    """
    if not scan_dir:
        return None
    try:
        hits = long_paths(scan_dir, limit)
    except Exception:
        return None
    if not hits:
        return None
    return ("%s file(s) in this scan are longer than %d characters. The viewer reads "
            "them, but Explorer, 7-Zip and plain scripts will not - move the scan to "
            "a shorter folder before sharing it."
            % ("%d+" % len(hits) if len(hits) >= LONG_PATH_REPORT_CAP else len(hits),
               limit))


def _manifest_unlisted(idx, cache=None):
    """
    How many files sit in a folder the manifest DOES cover but have no row of their own.

    Only the source folders the manifest itself names are counted, because those are
    the only ones his classifier walks: the Atlas cameras, full_scan.rec and
    wheel_assembler/ are outside the split by design and are not a gap. Counting them
    would turn every healthy scan into a warning.
    """
    cache = cache or _DirCache()
    claimed = set(idx.assign)
    folders = {os.path.dirname(p) for p in claimed}
    n = 0
    for folder in folders:
        for name in cache.listing(folder):
            if name.lower() in MANIFEST_NOT_A_FRAME:
                continue
            full = os.path.join(folder, name)
            if full not in claimed and cache.isfile(full):
                n += 1
    return n


# ------------------------------------------------------------------
# Export (only when the user asks for it)
# ------------------------------------------------------------------
def export_tree(index, dest_root, progress=None):
    """
    Write the WHOLE scan under `dest_root`, classified where the engineer's script
    classified it and preserved verbatim where it did not.

        <dest>/Axle_1/Left/Tread/tr_front_l_01/frame_0000.tif
        <dest>/Trash/Tread/tr_rear_l_01/frame_0100.tif
        <dest>/Unclassified/at_cam_01/frame_0001.tif

    Why it is not his destination layout any more
    ---------------------------------------------
    It used to be, and that cost most of the scan. Two independent losses, both
    measured on 2026-08-31T11-16-05 (10,347 files, 1664 MB):

      * his layout keys files by BASENAME only, so the four LMI folders and the four
        tread folders - which reuse the same frame names - landed on the same paths.
        2,956 classified files collapsed to 1,947 on disk: 1,009 silently
        overwritten. Adding the source folder as the last path element makes every
        destination unique, so nothing can overwrite anything.

      * his scan_files() only walks the four LMI, four tread and two tire_wall
        folders. The 15 Atlas camera folders, the LMI *_profile.csv files and
        wheel_assembler/triggers_data.csv - 7,391 files - were never in the manifest
        and so were never exported at all. They are copied here under Unclassified/
        with their original relative paths.

    The axle DECISIONS are still entirely his: which axle a file belongs to, and
    which side, is read from his manifest and never re-decided here. Only where the
    bytes are put changed, and only so that none of them are lost.

    Returns (n_classified, n_unclassified, total_bytes, overwritten). `overwritten`
    is a guard, not a feature: it must be 0, and the caller reports it if it is not.
    """
    dest_abs = os.path.abspath(dest_root)
    n_cls = n_unc = 0
    total_bytes = 0
    written = set()
    overwritten = 0

    def _copy(src, rel):
        """Copy one file to <dest_root>/<rel>, counting collisions. Never recurses
        into the destination, so exporting into the scan folder cannot loop."""
        nonlocal total_bytes, overwritten
        dest_dir = os.path.join(dest_root, rel)
        os.makedirs(lp(dest_dir), exist_ok=True)
        dest = os.path.join(dest_dir, os.path.basename(src))
        if dest in written:
            overwritten += 1
        written.add(dest)
        # The DESTINATION is the deeper of the two here - it gains Axle_1/Left/Tread/
        # <camera folder> over the source - so both ends go through lp().
        shutil.copy2(lp(src), lp(dest))
        total_bytes += lp_getsize(src)

    classified = sorted(index.pairs())          # v15: a shared frame goes to each axle
    claimed = {os.path.abspath(p) for p in index.assign}

    # everything in the scan folder that his walk never reached
    leftovers = []
    scan_dir = index.scan_dir
    if scan_dir and lp_isdir(scan_dir):
        for root, _dirs, files in lp_walk(scan_dir):
            if os.path.abspath(root).startswith(dest_abs + os.sep) \
                    or os.path.abspath(root) == dest_abs:
                continue                       # never copy the export into itself
            for name in files:
                p = os.path.join(root, name)
                if os.path.abspath(p) not in claimed:
                    leftovers.append(p)

    total = len(classified) + len(leftovers)
    done = 0

    for src, bucket in classified:
        done += 1
        if not lp_isfile(src):
            continue
        m = index.meta.get(src, {})
        media = m.get("media") or "unknown"
        folder = os.path.basename(os.path.dirname(src)) or "unknown"
        if bucket == TRASH:
            rel = os.path.join(TRASH, media, folder)
        else:
            # his folder name is Axle_1, not "Axle 1"
            rel = os.path.join(str(bucket).replace(" ", "_"),
                               m.get("side") or "unknown", media, folder)
        _copy(src, rel)
        n_cls += 1
        if progress is not None and (done % 50 == 0):
            progress(done, total)

    for src in leftovers:
        done += 1
        if not lp_isfile(src):
            continue
        rel = os.path.join(UNCLASSIFIED,
                           os.path.dirname(os.path.relpath(src, scan_dir)))
        _copy(src, rel)
        n_unc += 1
        if progress is not None and (done % 50 == 0):
            progress(done, total)

    if progress is not None:
        progress(total, total)
    return n_cls, n_unc, total_bytes, overwritten
