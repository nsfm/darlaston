# PyInstaller build description, shared by all three platforms.
#
# One spec rather than three: the differences between Linux, macOS and
# Windows here are a bundle wrapper and an icon, not what goes inside, and
# three files would drift the moment one of them gained a hidden import.
#
# Two things are deliberately NOT bundled:
#
#   * **The vendor SDK.** It ships with no licence or copyright notice of
#     any kind, so darlaston loads it at runtime from wherever the user
#     unpacked it. A frozen build must not change that, and the excludes
#     below make sure a stray copy on the build machine cannot be swept in.
#   * **Qt modules nothing imports.** PySide6 is large and the default
#     collection is larger still: WebEngine alone is over a hundred
#     megabytes of a browser this program has no use for.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
MACOS = sys.platform == "darwin"
WINDOWS = sys.platform.startswith("win")

# The interface is not all Python. These are the same files the wheel
# carries via [tool.setuptools.package-data]; a build missing them starts
# and then raises FileNotFoundError building its own stylesheet, because
# theme.stylesheet() asks icons.path_for for the combo-box chevron before
# a window ever opens.
datas = collect_data_files(
    "darlaston",
    includes=["ui/fonts/*", "ui/icons/*", "i18n/locale/*/*.po"])

hiddenimports = [
    # Reached only through __import__(module) in camera/toupcam.py, so
    # nothing static can see them. Harmless when absent: the loader tries
    # each brand in turn and raises SdkMissing if none is installed.
    "ctypes",
    "ctypes.util",
]

excludes = [
    # Qt subsystems this program never touches. Measured, by adding up what
    # they weigh in site-packages: 251 MB, against a 409 MB bundle with
    # them already gone. WebEngine is most of it -- a whole browser engine
    # shipped alongside a program that displays no HTML.
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras", "PySide6.Qt3DLogic",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtQuick3D",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtRemoteObjects", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtUiTools", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    # Toolkits that come along with matplotlib-adjacent packages and are a
    # second GUI stack inside a GUI app.
    "tkinter", "matplotlib", "PIL", "IPython", "pytest", "setuptools",
    # The vendor SDK is never redistributed. If a developer has one on
    # their path, it must not end up in a build that goes to strangers.
    "toupcam", "altaircam", "omegonprocam", "meadecam", "starshootg",
    "nncam", "mallincam", "risingcam", "ogmacam",
]

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)

# Qt's GTK platform theme, and the GTK stack it drags behind it. It exists
# to make Qt dialogs look like GNOME dialogs, which is not a thing this
# program wants -- every widget here is drawn from our own stylesheet. It
# is also the single worst thing to bundle: GTK links half the desktop, so
# a copy built on one distribution will happily load the wrong theme
# engine on another.
_GTK = ("libgtk-3", "libgdk-3", "libglycin", "libgdk_pixbuf",
        "qgtk3", "libcloudproviders")
a.binaries = TOC([e for e in a.binaries
                  if not any(n in e[0] for n in _GTK)])

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="darlaston",
    debug=False,
    strip=False,
    # UPX is off on purpose. It saves perhaps 15% and is a reliable way to
    # be flagged by antivirus heuristics on Windows and by Gatekeeper on
    # macOS, which is an expensive trade for a program people already have
    # to be talked through installing.
    upx=False,
    console=False,
    # x86_64 explicitly on macOS: see the note in the CI workflow. The two
    # architectures are built separately and joined with lipo, because
    # PySide6 does not ship universal2 wheels.
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="darlaston",
)

if MACOS:
    app = BUNDLE(
        coll,
        name="darlaston.app",
        icon=None,
        bundle_identifier="com.ndube.darlaston",
        info_plist={
            "CFBundleName": "darlaston",
            "CFBundleDisplayName": "darlaston",
            "CFBundleExecutable": "darlaston",
            # Retina. Without it the whole interface is drawn at 1x and
            # then scaled up, which on a hairline-based design is the
            # difference between crisp and smeared.
            "NSHighResolutionCapable": True,
            # It is a real windowed application, not a menu-bar agent.
            "LSUIElement": False,
            "NSRequiresAquaSystemAppearance": False,
            "LSMinimumSystemVersion": "11.0",
            "CFBundleDocumentTypes": [],
        },
    )
