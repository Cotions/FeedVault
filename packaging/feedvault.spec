# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: the Flask backend, the built React UI (its fonts are in
# frontend/dist/assets) and the userscript in one self-contained executable.
# Built by ./bundle.sh. The downloaders and ffmpeg are not in it: they stay
# separate programs, found on PATH or set in Settings.
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# (in the repo, folder in the bundle): config.static_dir() and
# config.bundled() look for the second one (backend/tests/test_frozen.py).
datas = [
    (os.path.join(ROOT, "frontend", "dist"), "static"),
    (os.path.join(ROOT, "userscript", "feedvault.user.js"), "userscript"),
]

a = Analysis(
    [os.path.join(ROOT, "backend", "app.py")],
    pathex=[os.path.join(ROOT, "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # Pillow opens a file with whichever plugin knows its format and
        # imports them by name (PIL.Image.init): thumbs.FORMATS, and the two
        # the JPEG one reaches for (a multi-picture file, EXIF).
        "PIL.Image",
        "PIL._imaging",
        "PIL.JpegImagePlugin",
        "PIL.PngImagePlugin",
        "PIL.WebPImagePlugin",
        "PIL.GifImagePlugin",
        "PIL.AvifImagePlugin",
        "PIL.MpoImagePlugin",
        "PIL.TiffImagePlugin",
    ],
    excludes=["tkinter", "matplotlib", "numpy", "pytest", "PIL.ImageTk", "PIL.ImageQt"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="feedvault",
    console=True,
    strip=False,
    upx=False,
    icon=None,
)
