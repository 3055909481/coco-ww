"""生成在线更新用的 update-manifest.json 和 Release 说明 (GitHub Actions 自动发版用, 本地也能跑)。

清单和 F:/coco-ww-release/pack_release.py 出的一模一样: <commit> 里每个 git 跟踪文件的 SHA256 + 大小,
版本号取 <commit> 里 config.py 的 version。只发清单不发整包: 在线更新按提交号从仓库下文件, 用不到 zip。

用法:
  python tools/release_manifest.py <输出目录> [--commit HEAD] [--prev-tag v1.0.4] [--full-url <整包下载页>]
输出: <输出目录>/update-manifest.json、<输出目录>/notes.md, 并在 stdout 打印 version=<版本> (给 Actions 读)。
"""

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile


def git(*args, binary=False):
    out = subprocess.run(["git", *args], check=True, capture_output=True)
    return out.stdout if binary else out.stdout.decode("utf-8").strip()


def build_manifest(commit):
    tar_bytes = git("archive", "--format=tar", commit, binary=True)
    files = {}
    version = None
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            data = tf.extractfile(member).read()
            if member.name == "config.py":
                found = re.search(rb'^version\s*=\s*["\']([^"\']+)["\']', data, re.MULTILINE)
                version = found and found.group(1).decode()
            files[member.name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    if not version:
        sys.exit("config.py 里没读到 version")
    return {"format": 1, "version": version, "commit": commit, "files": files}


# 不进在线更新清单的路径（整包 zip 里都有；在线更新时用不到）：
# - docs/ 是开发/使用文档，与程序运行无关，纯文档无需增量更新；
# - *.bat 是启动转发脚本，中文文件名在 CDN 镜像（jsdelivr 等）上会 404/403，
#   而且它是整包自带的入口，在线替换风险大于收益。
# 排除这些也保证清单里没有非 ASCII 路径，避免各下载源的中文路径兼容问题。
MANIFEST_EXCLUDE_PREFIXES = ("docs/",)
MANIFEST_EXCLUDE_SUFFIXES = (".bat",)


def _is_manifest_excluded(rel):
    if any(rel.startswith(prefix) for prefix in MANIFEST_EXCLUDE_PREFIXES):
        return True
    if any(rel.endswith(suffix) for suffix in MANIFEST_EXCLUDE_SUFFIXES):
        return True
    return False


def build_manifest_from_dir(tree_dir, version, commit):
    """从混淆产物目录生成清单（release 里的文件是 .pyc，不能从 git archive 读）。

    tree_dir 里的相对路径就是清单路径；version 由调用方从源码 config.py 读出后传入；
    commit 传 obfuscated 分支的提交号，pyappify 会按它从该分支 raw 下载这些 .pyc。
    """
    tree = os.path.abspath(tree_dir)
    files = {}
    for root, dirs, names in os.walk(tree):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for name in names:
            full = os.path.join(root, name)
            if not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, tree).replace(os.sep, "/")
            if _is_manifest_excluded(rel):
                continue
            with open(full, "rb") as f:
                data = f.read()
            files[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    if not version:
        sys.exit("混淆产物模式需要 --version（从源码 config.py 读）")
    return {"format": 1, "version": version, "commit": commit, "files": files}


def change_lines(prev_tag, commit):
    """上一版到这一版的提交标题, 去掉只改版本号的提交 (标题就是 vX.Y.Z)。

    commit 必须是源码分支（main）上的提交：清单里的 commit 指向 obfuscated 分支，
    那是 orphan 分支、没有 prev_tag 的祖先，git log 会失败。取不到变更就降级为空列表。
    """
    if not prev_tag or not commit:
        return []
    try:
        subjects = git("log", "--no-merges", "--format=%s", f"{prev_tag}..{commit}").splitlines()
    except subprocess.CalledProcessError:
        return []
    return [s for s in subjects if s.strip() and not re.fullmatch(r"v\d+(\.\d+)*", s.strip())]


def build_notes(version, prev_tag, source_commit, full_url, runtime_changed):
    changes = change_lines(prev_tag, source_commit)
    lines = [f"{changes[0] if changes else '小更新'}。" if len(changes) == 1 else "小更新。", ""]
    lines += ["## 下载和启动", ""]
    if runtime_changed:
        lines += ["⚠️ 这一版换了运行环境（requirements.txt 变了），在线更新补不了，要下载完整包。", ""]
    lines += [
        "1. 已经是 v1.0.2 及以上的：程序里会弹更新提示，点「更新」就行（也可以在「关于 → 应用更新」里手动检查）。",
        "2. 新装：下载本页附件 `coco-ww-vX.Y.Z.zip`（自带 Python 运行环境），解压到**纯英文路径**"
        "（例如 `D:\\coco-ww`），双击 `启动 coco-ww.bat`。",
        "",
        "附件 `update-manifest.json` 是在线更新用的文件清单，不用下载。",
        "",
        "## 更新内容",
        "",
    ]
    lines += [f"* {s}" for s in changes] or ["* （见提交记录）"]
    lines += [
        "",
        "## ⚠️ 注意",
        "",
        "本软件免费开源，如果你是花钱买到的，请立即退款。使用第三方工具有被封号的风险，请了解风险后再使用。",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir")
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--prev-tag", default="")
    parser.add_argument("--full-url", default="")
    parser.add_argument("--from-dir", default="",
                        help="从混淆产物目录生成清单（pyc 版），此时 --commit 传 obfuscated 分支提交号")
    parser.add_argument("--version", default="", help="--from-dir 模式下显式指定版本号")
    parser.add_argument("--source-commit", default="HEAD",
                        help="变更日志用哪个源码提交（main 分支）；--from-dir 时应传 main 的 HEAD，"
                             "不能用 obfuscated 分支的 --commit")
    args = parser.parse_args()

    if args.from_dir:
        version = args.version
        if not version:
            version_file = os.path.join(args.from_dir, "version.txt")
            if os.path.isfile(version_file):
                with open(version_file, encoding="utf-8") as f:
                    version = f.read().strip()
        manifest = build_manifest_from_dir(args.from_dir, version, args.commit)
        source_commit = git("rev-parse", args.source_commit)
    else:
        commit = git("rev-parse", args.commit)
        manifest = build_manifest(commit)
        source_commit = commit

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "update-manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=0, sort_keys=True)

    runtime_changed = False
    if args.prev_tag and source_commit:
        runtime_changed = bool(git("diff", "--name-only", args.prev_tag, source_commit, "--", "requirements.txt"))
    notes = build_notes(manifest["version"], args.prev_tag, source_commit, args.full_url, runtime_changed)
    with open(os.path.join(args.out_dir, "notes.md"), "w", encoding="utf-8") as f:
        f.write(notes)
    print(f"version={manifest['version']}")
    print(f"files={len(manifest['files'])}", file=sys.stderr)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    main()
