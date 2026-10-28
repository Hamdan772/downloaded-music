# PyInstaller spec for melograb.app (also builds a plain folder on Linux,
# which is how the packaging gets smoke-tested off-Mac).
#
#   pyinstaller --noconfirm packaging/melograb.spec
#
# Environment:
#   MELOGRAB_TOOLS_DIR     folder of extra executables (ffmpeg, ffprobe, deno)
#                          to bundle; default build/tools
#   MELOGRAB_SIGN_IDENTITY codesign identity ("Developer ID Application: ...");
#                          unset = ad-hoc signature
import os
import sys

from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 - provided by PyInstaller
sys.path.insert(0, ROOT)
from melograb import __version__  # noqa: E402

datas, binaries, hiddenimports = [], [], []
for package in ("ytmusicapi", "yt_dlp", "yt_dlp_ejs", "certifi"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
# ytdlp_updates compares against the bundled yt-dlp's metadata version.
datas += copy_metadata("yt-dlp")
datas += [
    (os.path.join(ROOT, "assets"), "assets"),
    (os.path.join(ROOT, "packaging", "THIRD_PARTY_NOTICES.md"), "."),
]

tools_dir = os.environ.get("MELOGRAB_TOOLS_DIR") or os.path.join(ROOT, "build", "tools")
if os.path.isdir(tools_dir):
    for name in sorted(os.listdir(tools_dir)):
        path = os.path.join(tools_dir, name)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            binaries.append((path, "bin"))
            print(f"melograb.spec: bundling {name}")

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "run_gui.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    # PIL loads _tkinter_finder dynamically when drawing images into Tk.
    hiddenimports=hiddenimports + ["melograb.gui", "melograb.selftest", "PIL._tkinter_finder", "PIL.ImageTk"],
    excludes=["pytest", "_pytest", "IPython", "matplotlib", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

identity = os.environ.get("MELOGRAB_SIGN_IDENTITY") or None
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="melograb",
    console=False,
    argv_emulation=False,
    codesign_identity=identity,
    entitlements_file=os.path.join(ROOT, "packaging", "entitlements.plist") if identity else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="melograb")  # noqa: F821

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="melograb.app",
        icon=os.path.join(ROOT, "assets", "melograb.icns"),
        bundle_identifier="app.melograb.desktop",
        version=__version__,
        info_plist={
            "CFBundleName": "melograb",
            "CFBundleDisplayName": "melograb",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.music",
            "NSHighResolutionCapable": True,
            # Follow the system light/dark appearance.
            "NSRequiresAquaSystemAppearance": False,
            "NSHumanReadableCopyright": "melograb — for personal use.",
            # Lets you drop a playlist CSV on the Dock icon / "Open With".
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "CSV playlist",
                    "CFBundleTypeRole": "Viewer",
                    "LSHandlerRank": "Alternate",
                    "LSItemContentTypes": ["public.comma-separated-values-text"],
                }
            ],
        },
    )
