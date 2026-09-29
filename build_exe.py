"""Build the packaged exe WITHOUT losing the runtime files.

PyInstaller's COLLECT step removes the whole ``dist/DGLabOSC`` directory, which
also deletes the user's ``config.json`` (saved devices, thresholds) and
``dglab_osc.log`` sitting next to the exe.  This wrapper backs them up first and
restores them after the build.

Usage:  python build_exe.py [--keep-log]
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / "dist" / "DGLabOSC"
RUNTIME_FILES = ("config.json", "dglab_osc.log")


def main() -> int:
    keep_log = "--keep-log" in sys.argv
    backup_dir = ROOT / "_research" / "_runtime_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    saved: dict[str, Path] = {}
    for name in RUNTIME_FILES:
        if name == "dglab_osc.log" and not keep_log:
            continue  # the log is diagnostics only; skip unless asked
        src = APP_DIR / name
        if src.is_file():
            dst = backup_dir / name
            shutil.copyfile(src, dst)
            saved[name] = dst
            print(f"backed up {name}")

    cmd = [sys.executable, "-m", "PyInstaller", "DGLabOSC.spec", "--noconfirm"]
    code = subprocess.call(cmd, cwd=ROOT)
    if code != 0:
        print(f"build failed (exit {code})")
        return code

    for name, dst in saved.items():
        shutil.copyfile(dst, APP_DIR / name)
        print(f"restored {name}")
    if not saved:
        print("no runtime files existed to restore")
    print(f"build ok: {APP_DIR / 'DGLabOSC.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())