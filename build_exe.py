from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / "dist" / "DGStudio"
RUNTIME_FILES = ("config.json", "dgstudio.log")
RUNTIME_DIRS = ("config",)


def main() -> int:
    keep_log = "--drop-log" not in sys.argv
    backup_dir = ROOT / "_research" / "_runtime_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    saved: dict[str, Path] = {}
    for name in RUNTIME_FILES:
        if name == "dgstudio.log" and not keep_log:
            continue
        src = APP_DIR / name
        if src.is_file():
            dst = backup_dir / name
            shutil.copyfile(src, dst)
            saved[name] = dst
            print(f"backed up {name}")
    for name in RUNTIME_DIRS:
        src = APP_DIR / name
        if src.is_dir():
            dst = backup_dir / name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            saved[name] = dst
            print(f"backed up {name}/")

    cmd = [sys.executable, "-m", "PyInstaller", "DGStudio.spec", "--noconfirm"]
    code = subprocess.call(cmd, cwd=ROOT)
    if code != 0:
        print(f"build failed (exit {code})")
        return code

    for name, dst in saved.items():
        if dst.is_dir():
            shutil.copytree(dst, APP_DIR / name, dirs_exist_ok=True)
        else:
            shutil.copyfile(dst, APP_DIR / name)
        print(f"restored {name}")
    if not saved:
        print("no runtime files existed to restore")
    print(f"build ok: {APP_DIR / 'DGStudio.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
