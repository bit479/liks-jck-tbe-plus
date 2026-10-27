#!/usr/bin/env python
"""把程序打包成单文件 exe（Windows）。

用法：
    python build_exe.py            # 用当前解释器打包
    python build_exe.py --console  # 保留控制台，调试用（能看到报错）

产物：dist/TBeAutoLike-<版本>-win64.exe

几个必须这么做的地方：
1. 构建目录用**带时间戳的新目录**——本机安全策略禁止删除已存在目录，
   PyInstaller 默认的 --clean 去删 build/ 会直接被拦下。
2. --add-data 把 assets/ 打进去（窗口图标要用），代码里按 sys._MEIPASS 取。
3. 排除 PIL/numpy/pandas 等：打包环境里装着它们（别的工具在用），
   不排除会白白多出几十 MB。
4. 打完跑一次 `exe --selftest`，确认配置目录指向 exe 旁边而不是临时解包目录。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "TBeAutoLike"

EXCLUDES = (
    "PIL", "numpy", "pandas", "matplotlib", "scipy", "pymupdf", "fitz",
    "win32com", "openpyxl", "xlrd", "xlwt", "pyodbc", "pytest", "IPython",
)


def version() -> str:
    sys.path.insert(0, str(ROOT))
    from tbeplus import __version__

    return __version__


def main() -> int:
    ver = version()
    exe_name = f"{NAME}-{ver}-win64"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work = ROOT / "build" / f"work_{stamp}"
    dist = ROOT / "build" / f"dist_{stamp}"
    for d in (work, dist):
        d.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--name", exe_name,
        "--icon", str(ROOT / "assets" / "app.ico"),
        "--add-data", f"{ROOT / 'assets'};assets",
        "--workpath", str(work),
        "--distpath", str(dist),
        "--paths", str(ROOT),
    ]
    cmd.append("--console" if "--console" in sys.argv else "--windowed")
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(str(ROOT / "app.py"))

    print("打包中…（首次约 1-2 分钟）")
    proc = subprocess.run(cmd, cwd=str(ROOT))
    if proc.returncode != 0:
        print("打包失败，退出码", proc.returncode)
        return proc.returncode

    built = dist / f"{exe_name}.exe"
    if not built.is_file():
        print("没找到产物：", built)
        return 1

    out_dir = ROOT / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{exe_name}.exe"
    shutil.copy2(built, target)          # 覆盖写可以，删除不行

    size = target.stat().st_size / 1024 / 1024
    print(f"\n产物：{target}")
    print(f"体积：{size:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
