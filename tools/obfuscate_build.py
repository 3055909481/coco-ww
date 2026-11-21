"""把源码树构建成混淆后的 release 目录（pyc only，无明文 .py）。

用法：
    python tools/obfuscate_build.py --out <输出目录> [--python py:-3.12]

做的事情：
  1. 复制非源码资源（configs/ assets/ icons/ docs/ i18n/ ok/ html/ *.txt *.bat 等）；
  2. 混淆前先把内置角色的原始源码加密封进 src/_builtin_sources.dat
     （"查看/编辑内置代码"功能在 release 里靠它提供原文）；
  3. 对 src/ pyappify/ config.py main.py main_debug.py 逐个 AST 混淆，
     再用 Python 3.12 编译成 hash-based sourceless .pyc，原位替代 .py；
  4. 把启动脚本里的 main.py 改成 main.pyc；
  5. 输出目录里不再有任何项目自身的 .py 源码。

注意：
- 必须用与目标运行环境一致的 Python 编译（发布包自带 3.12），默认尝试 py -3.12。
- ok/ 是第三方框架源码，不属于本项目核心资产，按原样复制（它本身是公开开源框架）。
- tools/ tests/ .github/ 不进 release。
"""

import argparse
import ast
import io
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from obfuscate import obfuscate_source  # noqa: E402
from _obf_common import BUILTIN_DATA_REL, seal_text  # noqa: E402

# 要混淆的源码（项目自有核心资产）
OBFUSCATE_DIRS = ("src", "pyappify")
OBFUSCATE_FILES = ("config.py", "main.py", "main_debug.py")

# 原样复制的目录/文件（第三方框架、用户数据、资源）
COPY_DIRS = ("configs", "assets", "icons", "docs", "i18n", "ok", "html", "web", "exe")
COPY_FILES = (
    "requirements.txt", "LICENSE.txt", "README.md",
    "启动 coco-ww.bat", "调试启动(带控制台).bat",
)

# 含 nonlocal 的角色文件混淆时跳过局部变量重命名（obfuscate_source 内部已处理），
# 这里列出仅用于日志提示。
KNOWN_SKIP_HINTS = ("src/char/Linnai.py",)

SKIP_SOURCE_NAMES = {"__pycache__"}


def resolve_compiler(spec):
    """返回 (命令前缀, 版本描述)。"""
    candidates = []
    if spec:
        candidates.append(spec)
    candidates += ["py -3.12", "python3.12"]
    for candidate in candidates:
        parts = candidate.split()
        try:
            out = subprocess.run(parts + ["-c",
                                 "import sys;assert sys.version_info[:2]==(3,12);print('.'.join(map(str,sys.version_info[:3])))"],
                                 capture_output=True, text=True, timeout=20)
            if out.returncode == 0:
                version = out.stdout.strip().splitlines()[-1]
                return parts, f"Python {version} ({candidate})"
        except (OSError, subprocess.SubprocessError):
            continue
    raise RuntimeError("找不到 Python 3.12；发布包运行环境是 3.12，必须用同版本编译 pyc")


# 批量编译：把 (源码, 目标 pyc, co_filename) 列表以 JSON 从 stdin 传给单个 3.12 进程，
# 用 compile()+marshal 直接写 pyc。全程不产生临时 .py 文件，因此既能精确控制 co_filename，
# 又不会在工作区里反复创建/删除文件。
_BATCH_COMPILE_SNIPPET = r"""
import importlib.util, json, marshal, os, sys
items = json.loads(sys.stdin.buffer.read().decode('utf-8'))
MAGIC = importlib.util.MAGIC_NUMBER
for item in items:
    code = compile(item['source'], item['filename'], 'exec')
    dest = item['dest']
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(MAGIC)
        f.write((1).to_bytes(4, 'little'))  # flags: hash-based (UNCHECKED_HASH)
        f.write(b'\0' * 8)                  # hash 占位；unchecked 模式运行时忽略
        f.write(marshal.dumps(code))
print('compiled', len(items))
"""


def compile_batch(compiler, items):
    """items: [(source_code, dest_pyc, co_filename)]，一次性编译写出所有 pyc。"""
    payload = [
        {"source": source, "dest": str(dest), "filename": filename}
        for source, dest, filename in items
    ]
    proc = subprocess.run(compiler + ["-c", _BATCH_COMPILE_SNIPPET],
                          input=json.dumps(payload).encode("utf-8"),
                          capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError("批量编译 pyc 失败:\n" + proc.stderr.decode("utf-8", "replace")[-2000:])


def collect_builtin_sources():
    """混淆前读取内置角色的原始源码，返回 {类名: 源码}。"""
    mapping = {}
    char_dir = ROOT / "src" / "char"
    for path in sorted(char_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
        if not classes:
            continue
        for class_name in classes:
            mapping[class_name] = source
    return mapping


def copy_plain_resources(out):
    copied = []
    for name in COPY_DIRS:
        src = ROOT / name
        if src.is_dir():
            dst = out / name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            copied.append(name + "/")
    for name in COPY_FILES:
        src = ROOT / name
        if src.is_file():
            shutil.copy2(src, out / name)
            copied.append(name)
    # 顶层其它 .py（如 pyproject 之类）不复制；程序入口由混淆阶段生成
    return copied


def iter_source_files():
    for dirname in OBFUSCATE_DIRS:
        base = ROOT / dirname
        for path in base.rglob("*.py"):
            if any(part in SKIP_SOURCE_NAMES for part in path.parts):
                continue
            yield path
    for name in OBFUSCATE_FILES:
        path = ROOT / name
        if path.is_file():
            yield path


def rewrite_bat(out):
    """启动脚本改指向 main.pyc / main_debug.pyc。"""
    for name in ("启动 coco-ww.bat", "调试启动(带控制台).bat"):
        path = out / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        # 先替换更长的 main_debug.py，避免 main.py 把它的前缀改坏
        changed = text.replace("main_debug.py", "main_debug.pyc").replace("main.py", "main.pyc")
        if changed != text:
            path.write_text(changed, encoding="utf-8")


def build(out_dir, compiler_spec=None, quiet=False):
    out = Path(out_dir).resolve()
    if out == ROOT or out in {ROOT / name for name in COPY_DIRS} | {
            ROOT / name for name in OBFUSCATE_DIRS}:
        raise RuntimeError("输出目录不能是源码根目录或被复制的源目录")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    compiler, compiler_desc = resolve_compiler(compiler_spec)
    if not quiet:
        print(f"[构建] 编译器: {compiler_desc}")
        print(f"[构建] 输出: {out}")

    copied = copy_plain_resources(out)
    if not quiet:
        print(f"[构建] 原样复制资源: {', '.join(copied) or '(无)'}")

    # 1) 先封存内置角色原始源码（必须在混淆前读）
    builtin = collect_builtin_sources()
    data_path = out / BUILTIN_DATA_REL
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_bytes(seal_text(builtin))
    if not quiet:
        print(f"[构建] 内置源码加密: {len(builtin)} 个类 -> {data_path.name} ({data_path.stat().st_size} 字节)")

    # 2) 先全部混淆（构建端解释器），收集批量编译任务
    files = list(iter_source_files())
    failures = []
    compile_items = []
    for index, path in enumerate(files, 1):
        rel = path.relative_to(ROOT)
        source = path.read_text(encoding="utf-8")
        try:
            obfuscated = obfuscate_source(source, b"coco-source-obfuscation-key-v1")
            ast.parse(obfuscated)  # AST 变换后语法必须有效
        except Exception as exc:
            failures.append((str(rel), f"混淆失败: {exc}"))
            continue
        dest = out / rel.with_suffix(".pyc")
        # co_filename 用模块对应的 .py 路径，traceback 可读；不落盘到输出目录
        compile_items.append((obfuscated, dest, str(rel.with_suffix(".py")).replace("\\", "/")))
        if not quiet and index % 25 == 0:
            print(f"[构建] 已混淆 {index}/{len(files)} ...")
    if failures:
        msg = "\n".join(f"  - {name}: {err}" for name, err in failures)
        raise RuntimeError(f"混淆构建失败 {len(failures)} 个文件:\n{msg}")

    # 3) 用目标 Python 一次性批量编译成 pyc（无临时文件）
    try:
        compile_batch(compiler, compile_items)
    except Exception as exc:
        raise RuntimeError(f"批量编译失败: {exc}")
    compiled = len(compile_items)

    # 4) 输出目录不应残留任何项目自身 .py
    leftovers = []
    for dirname in OBFUSCATE_DIRS:
        leftover_dir = out / dirname
        if leftover_dir.exists():
            leftovers += [str(p.relative_to(out)) for p in leftover_dir.rglob("*.py")]
    leftovers += [n for n in OBFUSCATE_FILES if (out / n).is_file()]
    if leftovers:
        raise RuntimeError("输出目录仍残留明文源码: " + ", ".join(leftovers))

    rewrite_bat(out)

    # 版本号不是敏感信息：写一份明文，供更新器/清单读取，避免 import config 触发重依赖
    version_match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']',
                              (ROOT / "config.py").read_text(encoding="utf-8"), re.MULTILINE)
    if not version_match:
        raise RuntimeError("config.py 里没有 version = \"...\"")
    (out / "version.txt").write_text(version_match.group(1) + "\n", encoding="utf-8")

    if not quiet:
        print(f"[构建] 完成: {compiled} 个源码文件 -> .pyc，无明文 .py")
    return {"out": str(out), "compiled": compiled, "builtin_classes": len(builtin),
            "compiler": compiler_desc}


def main():
    parser = argparse.ArgumentParser(description="构建混淆后的 release 目录")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--python", default="py -3.12", help="Python 3.12 解释器命令")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    try:
        result = build(args.out, args.python, args.quiet)
    except Exception as exc:
        print(f"[构建失败] {exc}", file=sys.stderr)
        return 1
    if not args.quiet:
        print("[构建成功]", result)
    return 0


def _force_utf8_io():
    """Windows 控制台默认 charmap，打印中文会崩；强制 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


if __name__ == "__main__":
    _force_utf8_io()
    sys.exit(main())
