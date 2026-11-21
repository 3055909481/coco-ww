"""把混淆产物 + 干净的 Python 3.12 运行环境打成可运行的整包 zip。

用法（Windows，需本机有 py -3.12）：
    python -X utf8 tools/pack_release.py --obf-dir build\\release

产物：仓库根目录的 coco-ww-vX.Y.Z.zip（版本号从 --obf-dir/version.txt 读）。

原理：
- 发布包自带 python/ 目录，启动脚本用 python\\pythonw.exe 直接起程序；
- 所以 python/ 必须是一个完整的、能独立找到 site-packages 的 Python 运行环境。
- 做法：建干净 venv 装 requirements.txt，把其 site-packages 合并进"复制的基础 Python 目录"
  的 Lib/site-packages。这样既保持 python\\pythonw.exe 路径不变，又只带项目需要的依赖。

注意：整包必须在 Windows 上打（requirements 含 pywin32/comtypes/pycaw 等 Windows 专属包）。
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def run(cmd, **kwargs):
    print("[pack] $", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kwargs)


def find_python312(explicit=None):
    candidates = []
    if explicit:
        candidates.append(explicit.split())
    candidates += [["py", "-3.12"], ["python3.12"], ["python"]]
    for candidate in candidates:
        try:
            out = subprocess.run(candidate + ["-c",
                                 "import sys;assert sys.version_info[:2]==(3,12);print(sys.prefix)"],
                                 capture_output=True, text=True, timeout=30)
            if out.returncode == 0:
                return candidate, out.stdout.strip().splitlines()[-1]
        except (OSError, subprocess.SubprocessError):
            continue
    raise RuntimeError("找不到 Python 3.12（需要 py -3.12）")


def read_version(obf_dir):
    version_file = Path(obf_dir) / "version.txt"
    if version_file.is_file():
        return version_file.read_text(encoding="utf-8").strip()
    config_py = (ROOT / "config.py").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', config_py, re.MULTILINE)
    if not match:
        raise RuntimeError("读不到版本号")
    return match.group(1)


def build(obf_dir, out_zip=None, clean=True, python_spec=None):
    obf_dir = Path(obf_dir).resolve()
    if not obf_dir.is_dir():
        raise RuntimeError(f"混淆产物目录不存在: {obf_dir}")
    version = read_version(obf_dir)
    out_zip = Path(out_zip) if out_zip else ROOT / f"coco-ww-{version}.zip"

    py_cmd, py_prefix = find_python312(python_spec)
    print(f"[pack] Python 3.12: {py_prefix} ({' '.join(py_cmd)})")
    print(f"[pack] 版本: {version} -> {out_zip}")

    work = Path(tempfile.mkdtemp(prefix="coco-pack-"))
    try:
        venv = work / "venv"
        print("[pack] 建干净 venv ...")
        run(py_cmd + ["-m", "venv", str(venv)])

        venv_py = venv / "Scripts" / "python.exe"
        print("[pack] 安装 requirements.txt ...")
        run([str(venv_py), "-m", "pip", "install", "--disable-pip-version-check",
             "-r", str(ROOT / "requirements.txt")])

        # 组装 python/ ：复制基础解释器完整目录，清掉其 site-packages 里的第三方包，
        # 再合并 venv 的 site-packages。
        python_dir = work / "stage" / "python"
        print("[pack] 复制 Python 基础运行环境 ...")
        shutil.copytree(py_prefix, python_dir,
                        ignore=shutil.ignore_patterns("__pycache__"))

        base_sp = python_dir / "Lib" / "site-packages"
        if base_sp.exists():
            shutil.rmtree(base_sp)
        base_sp.mkdir(parents=True)

        venv_sp = venv / "Lib" / "site-packages"
        print("[pack] 合并依赖到 python/Lib/site-packages ...")
        for item in venv_sp.iterdir():
            if item.name == "__pycache__":
                continue
            dest = base_sp / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

        # 校验组装好的 python 能导入项目关键依赖
        print("[pack] 校验运行环境 ...")
        # 只校验模块名确定的核心包：能导入它们就证明 site-packages 合并成功。
        # 不校验 PySide6/openvino，避免发行包的顶层模块名细节导致误判。
        check = subprocess.run([str(python_dir / "python.exe"), "-c",
                                "import cv2, numpy; print('runtime OK')"],
                               capture_output=True, text=True, timeout=120)
        if check.returncode != 0:
            raise RuntimeError("运行环境校验失败:\n" + check.stderr[-1000:])
        print("[pack]", check.stdout.strip())

        # 合并混淆产物到 stage 根目录（python/ 已就位，其余是 pyc + 资源）
        stage = work / "stage"
        print("[pack] 合并混淆产物 ...")
        for item in obf_dir.iterdir():
            dest = stage / item.name
            if item.name == "python":
                continue
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

        # 压缩
        print("[pack] 压缩整包 ...")
        out_zip.parent.mkdir(parents=True, exist_ok=True)
        if out_zip.exists():
            out_zip.unlink()
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(stage).as_posix())

        size_mb = out_zip.stat().st_size / 1024 / 1024
        print(f"[pack] 完成: {out_zip} ({size_mb:.1f} MB)")
        return {"zip": str(out_zip), "version": version, "size_mb": round(size_mb, 1)}
    finally:
        if clean:
            shutil.rmtree(work, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="打可运行整包（含 Python 运行环境）")
    parser.add_argument("--obf-dir", required=True, help="混淆产物目录（obfuscate_build.py 的输出）")
    parser.add_argument("--out", default="", help="输出 zip 路径（默认 coco-ww-<版本>.zip）")
    parser.add_argument("--python", default="", help="Python 3.12 解释器命令（默认自动探测）")
    parser.add_argument("--keep-work", action="store_true", help="保留临时目录（调试用）")
    args = parser.parse_args()
    try:
        result = build(args.obf_dir, args.out or None, clean=not args.keep_work, python_spec=args.python)
    except Exception as exc:
        print(f"[pack 失败] {exc}", file=sys.stderr)
        return 1
    print("[pack 成功]", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
