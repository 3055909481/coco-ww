"""构建端与运行时共用的混淆常量与编解码工具（纯标准库）。

/data 文件里放的是各内置角色"未混淆的原始源码"，供程序的"查看/编辑内置代码"功能使用。
release 里源码本体已编译为 .pyc，所以这些原始文本必须单独加密存放。

注意：这是源码防护（提高逆向成本），不是密码学安全；密钥随程序分发，无法防止有心人提取。
真正要保护的是"随手打开 release 目录就能看到核心逻辑"这种情况。
"""

import base64
import zlib

# 构建与运行时必须一致。改动后要重新构建 release。
OBFUSCATION_KEY = b"coco-ww|builtin-src|v1|k39f7a2b"

BUILTIN_DATA_REL = "src/_builtin_sources.dat"


def seal_text(mapping: dict) -> bytes:
    """把 {类名: 源码} 封成二进制数据文件。"""
    import json
    raw = json.dumps(mapping, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    zipped = zlib.compress(raw, 9)
    key = OBFUSCATION_KEY
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(zipped))
    return base64.b64encode(xored)


def unseal_text(data: bytes) -> dict:
    """seal_text 的逆操作。"""
    import json
    xored = base64.b64decode(data)
    key = OBFUSCATION_KEY
    zipped = bytes(b ^ key[i % len(key)] for i, b in enumerate(xored))
    return json.loads(zlib.decompress(zipped).decode("utf-8"))
