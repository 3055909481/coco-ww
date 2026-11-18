"""在线更新的最后一步: 等 coco-ww 退出, 换文件, 再按原命令行重启。

由 pyappify.update_to_version 拷到 cache/update/<版本>/ 后用 `pythonw -X utf8 -I` 起,
所以只能用标准库, 也不能 import 程序里的任何模块。参数是同目录的 plan.json。

换文件是一批的: 原文件先挪进 backup/, 新文件再挪到位; 中途任何一步失败就按记录全部挪回去,
程序还是原来那一版。日志写 logs/update.log。
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from ctypes import wintypes
from pathlib import Path

WAIT_EXIT_SECONDS = 180
SYNCHRONIZE = 0x00100000
WAIT_TIMEOUT = 0x00000102
ERROR_ACCESS_DENIED = 5
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
NOTICE_ENV = ("PYAPPIFY_APP_STARTING_VERSION", "PYAPPIFY_APP_VERSION", "PYAPPIFY_UPDATE_NOTE")


class Log:
    def __init__(self, root):
        self.path = Path(root) / "logs" / "update.log"

    def __call__(self, message):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as file:
                file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        except OSError:
            pass


def wait_for_exit(pid, timeout):
    """True = 进程已经退了。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        if ctypes.get_last_error() == ERROR_ACCESS_DENIED:
            time.sleep(10)  # 打不开句柄等不了, 给它点时间自己退
        return True
    try:
        return kernel32.WaitForSingleObject(handle, int(timeout * 1000)) != WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def move(src, dst):
    """os.replace, 被杀毒软件之类短暂占住时重试几秒。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(20):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)


def rollback(root, files_dir, backup_dir, journal, log):
    for rel, moved_old, placed_new in reversed(journal):
        dst = root / rel
        try:
            if placed_new and dst.exists():
                move(dst, files_dir / rel)  # 新文件放回暂存区, 下次重试不用再下
            if moved_old:
                move(backup_dir / rel, dst)
        except Exception:
            log(f"rollback {rel} failed:\n{traceback.format_exc()}")


def remove_empty_dirs(root, deleted):
    for rel in deleted:
        folder = (root / rel).parent
        while folder != root and root in folder.parents:
            try:
                children = list(folder.iterdir())
                if [child.name for child in children] == ["__pycache__"]:
                    shutil.rmtree(children[0])
                folder.rmdir()
            except OSError:
                break
            folder = folder.parent


def apply(plan, log):
    root = Path(plan["app_root"])
    files_dir = Path(plan["files_dir"])
    backup_dir = Path(plan["backup_dir"])
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    journal = []
    try:
        for rel in plan["replace"]:
            src, dst = files_dir / rel, root / rel
            if not src.is_file():
                raise FileNotFoundError(f"暂存区少了 {rel}")
            moved_old = dst.exists()
            if moved_old:
                move(dst, backup_dir / rel)
            journal.append([rel, moved_old, False])
            move(src, dst)
            journal[-1][2] = True
        for rel in plan["delete"]:
            dst = root / rel
            if dst.exists():
                move(dst, backup_dir / rel)
                journal.append([rel, True, False])
    except Exception:
        log(f"apply failed, rolling back {len(journal)} files:\n{traceback.format_exc()}")
        rollback(root, files_dir, backup_dir, journal, log)
        return False
    remove_empty_dirs(root, plan["delete"])
    return True


def restart(plan, updated, log):
    spec = plan.get("restart")
    if not spec:
        return
    env = dict(os.environ)
    for key in NOTICE_ENV:
        env.pop(key, None)
    if updated:
        # 框架 (ok/ui/qt/util/pyappify_startup.py) 读这三个变量, 在关于页显示「更新成功」和更新说明
        env["PYAPPIFY_APP_STARTING_VERSION"] = plan.get("from") or ""
        env["PYAPPIFY_APP_VERSION"] = plan["to"]
        env["PYAPPIFY_UPDATE_NOTE"] = json.dumps(plan.get("notes") or [], ensure_ascii=False)
    try:
        subprocess.Popen(spec["cmd"], cwd=spec["cwd"], env=env, close_fds=True,
                         creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
        log(f"restarted: {spec['cmd']}")
    except Exception:
        log(f"restart failed:\n{traceback.format_exc()}")


def main(plan_path):
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    log = Log(plan["app_root"])
    log(f"update {plan.get('from')} -> {plan['to']}: replace {len(plan['replace'])}, "
        f"delete {len(plan['delete'])}, waiting for pid {plan['pid']}")
    if not wait_for_exit(plan["pid"], WAIT_EXIT_SECONDS):
        log(f"pid {plan['pid']} still running after {WAIT_EXIT_SECONDS}s, update not applied")
        return 1
    try:
        updated = apply(plan, log)
    except Exception:
        log(f"apply crashed:\n{traceback.format_exc()}")
        updated = False
    if updated:
        shutil.rmtree(plan["files_dir"], ignore_errors=True)
        log(f"updated to {plan['to']}")
    restart(plan, updated, log)
    return 0 if updated else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
