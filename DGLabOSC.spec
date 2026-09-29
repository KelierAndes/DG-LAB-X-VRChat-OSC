# PyInstaller spec for the DG-Lab × VRChat OSC console.
# Build:  .venv/Scripts/python -m PyInstaller DGLabOSC.spec --noconfirm
#
# Notes:
# - win32more is pure Python and resolves runtime classes dynamically via
#   importlib (GetRuntimeClassName -> module), so every Microsoft.* / UI
#   namespace that can materialize at runtime must be bundled.
# - win32more/dll/x64/Microsoft.WindowsAppRuntime.Bootstrap.dll and
#   win32more/winui3/app.xaml must ship inside the bundle at their original
#   package-relative paths.
# - Windows App Runtime must be installed on the target machine
#   (win32more is framework-dependent; the bootstrap DLL finds it).
# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

import os

hiddenimports = (
    collect_submodules("win32more.Microsoft")
    + collect_submodules("win32more.Windows.Foundation")
    + collect_submodules("win32more.Windows.Graphics")
    + collect_submodules("win32more.Windows.UI")
    + collect_submodules("winrt")
    + collect_submodules("bleak")
    + collect_submodules("pythonosc")
    + collect_submodules("websockets")
    + collect_submodules("qrcode")
)

# 页面 XAML 骨架是数据文件，运行时由 ui.paths 从 _MEIPASS/xaml 解析
datas = collect_data_files("win32more") + [
    (os.path.join(SPECPATH, "xaml", name), "xaml")
    for name in sorted(os.listdir(os.path.join(SPECPATH, "xaml")))
    if name.endswith(".xaml")
]
binaries = collect_dynamic_libs("win32more")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DGLabOSC",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DGLabOSC",
)
