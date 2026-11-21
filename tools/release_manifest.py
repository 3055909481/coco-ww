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


def change_lines(prev_tag, commit):
    """上一版到这一版的提交标题, 去掉只改版本号的提交 (标题就是 vX.Y.Z)。"""
    if not prev_tag:
        return []
    subjects = git("log", "--no-merges", "--format=%s", f"{prev_tag}..{commit}").splitlines()
    return [s for s in subjects if s.strip() and not re.fullmatch(r"v\d+(\.\d+)*", s.strip())]


def build_notes(version, prev_tag, commit, full_url, runtime_changed):
    changes = change_lines(prev_tag, commit)
    lines = [f"{changes[0] if changes else '小更新'}。" if len(changes) == 1 else "小更新。", ""]
    lines += ["## 下载和启动", ""]
    if runtime_changed:
        lines += ["⚠️ 这一版换了运行环境（requirements.txt 变了），在线更新补不了，要下载完整包。", ""]
    lines += [
        "1. 已经是 v1.0.2 及以上的：程序里会弹更新提示，点「更新」就行（也可以在「关于 → 应用更新」里手动检查）。",
        "2. 新装：这一版只有在线更新清单，没有完整包。先到"
        + (f" [完整包下载页]({full_url}) " if full_url else "带 zip 附件的版本 ")
        + "下载 `coco-ww-vX.Y.Z.zip`，解压到**纯英文路径**（例如 `D:\\coco-ww`），双击 `启动 coco-ww.bat`，"
        "再在程序里更新到这一版。",
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
    args = parser.parse_args()

    commit = git("rev-parse", args.commit)
    manifest = build_manifest(commit)
    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "update-manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=0, sort_keys=True)

    runtime_changed = False
    if args.prev_tag:
        runtime_changed = bool(git("diff", "--name-only", args.prev_tag, commit, "--", "requirements.txt"))
    notes = build_notes(manifest["version"], args.prev_tag, commit, args.full_url, runtime_changed)
    with open(os.path.join(args.out_dir, "notes.md"), "w", encoding="utf-8") as f:
        f.write(notes)
    print(f"version={manifest['version']}")
    print(f"files={len(manifest['files'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
