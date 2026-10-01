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
    # 模块运行期（exe 旁 modules/ 的 plugin.py）导入的包不在主程序静态导入图里，
    # 需显式收集：dglab.mapping / dglab.params 等仅被模块引用的引擎子模块
    + collect_submodules("dglab")
)

# 页面 XAML 骨架是数据文件，运行时由 ui.paths 从 _MEIPASS/xaml 解析
datas = collect_data_files("win32more") + [
    (os.path.join(SPECPATH, "xaml", name), "xaml")
    for name in sorted(os.listdir(os.path.join(SPECPATH, "xaml")))
    if name.endswith(".xaml")
]

# 联动模块不进包体：modules/ 整个文件夹由 build_exe.py 复制到产物根（与 exe 同级），
# 用户可直接查看 / 替换 / 投放模块；运行期插件宿主扫描 exe 旁 modules/，
# 包导入（modules.<id>.server）由冻结态引导把 exe 目录插入 sys.path 解析。
# excludes 防止 ui.link_page 的静态导入把 modules 收进 PYZ 遮蔽用户侧文件。
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
    excludes=["modules"],
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
