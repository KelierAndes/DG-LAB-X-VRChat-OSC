# PyInstaller spec for DGStudio (DG-Lab × VRChat OSC console).
# Build:  .venv/Scripts/python -m PyInstaller DGStudio.spec --noconfirm
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
    + collect_submodules("modules")
)

# 页面 XAML 骨架是数据文件，运行时由 ui.paths 从 _MEIPASS/xaml 解析
datas = collect_data_files("win32more") + [
    (os.path.join(SPECPATH, "xaml", name), "xaml")
    for name in sorted(os.listdir(os.path.join(SPECPATH, "xaml")))
    if name.endswith(".xaml")
]

# 联动模块全部打进包内（_MEIPASS/modules）：插件宿主在冻结态扫描该目录发现模块。
# 显式逐文件收集，跳过 __pycache__；collect_submodules("modules") 负责包形式模块的
# PYZ 兜底（单文件模块无 __init__.py，靠这里的源文件以路径加载）。
MODULES_ROOT = os.path.join(SPECPATH, "modules")
datas += [
    (os.path.join(dirpath, fn),
     ("modules" if dirpath == MODULES_ROOT
      else "modules/" + os.path.relpath(dirpath, MODULES_ROOT).replace("\\", "/")))
    for dirpath, dirnames, files in os.walk(MODULES_ROOT)
    if "__pycache__" not in dirpath
    for fn in files
    if not fn.endswith((".pyc", ".pyo"))
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
    name="DGStudio",
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
    name="DGStudio",
)
