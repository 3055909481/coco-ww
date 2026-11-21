"""内置角色原始源码的安全读取（release 混淆版专用）。

release 里 src/char/*.py 被混淆编译成 .pyc，`inspect.getsource()` 已不可用；
但"查看/编辑内置代码"功能需要展示原始源码文本。构建时把这些原始文本加密存进
src/_builtin_sources.dat，本模块在运行时按需解密并缓存。

源码开发状态下数据文件不存在，调用方会回退到 inspect，行为与原来完全一致。
"""

import base64
import json
import zlib
from pathlib import Path

# 与 tools/_obf_common.py 中的 OBFUSCATION_KEY 保持一致
_KEY = b"coco-ww|builtin-src|v1|k39f7a2b"
_DATA_FILE = Path(__file__).resolve().parents[1] / "_builtin_sources.dat"

_cache = None


def _load():
    global _cache
    if _cache is not None:
        return _cache
    _cache = {}
    try:
        data = _DATA_FILE.read_bytes()
        xored = base64.b64decode(data)
        zipped = bytes(b ^ _KEY[i % len(_KEY)] for i, b in enumerate(xored))
        raw = zlib.decompress(zipped).decode("utf-8")
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            _cache = loaded
    except (OSError, ValueError, zlib.error):
        _cache = {}
    return _cache


def get_builtin_source(class_name):
    """按类名取内置角色的原始源码；没有数据文件/查不到时返回 None。"""
    return _load().get(class_name)


def has_secure_source():
    """release 混淆版里数据文件是否存在。"""
    return bool(_load())
