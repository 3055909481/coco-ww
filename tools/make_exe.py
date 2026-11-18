"""生成 python\\coco-ww.exe: pythonw.exe 的副本, 换上 coco-ww 的图标和版本信息。

任务管理器的进程名取 exe 文件名, 「进程」页显示的名字取版本信息里的 FileDescription,
所以光复制改名不够, 还要改资源。python\\ 不进 git, 重装运行环境后重跑一次:
    python\\python.exe -X utf8 tools\\make_exe.py
改资源会让 Python 官方签名失效, 先把签名整个摘掉, 当普通未签名程序。
"""
import os
import shutil
import struct
import sys

import win32api
import win32con

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'python', 'pythonw.exe')
DST = os.path.join(ROOT, 'python', 'coco-ww.exe')
ICO = os.path.join(ROOT, 'icons', 'icon.ico')
NAME = 'coco-ww'
LANG = 0x0804  # 简体中文
CODEPAGE = 1200  # Unicode


def strip_signature(path):
    with open(path, 'r+b') as f:
        data = bytearray(f.read())
        pe = struct.unpack_from('<I', data, 0x3C)[0]
        assert data[pe:pe + 4] == b'PE\0\0'
        opt = pe + 24
        magic = struct.unpack_from('<H', data, opt)[0]
        dirs = opt + (112 if magic == 0x20B else 96)
        sec = dirs + 4 * 8  # IMAGE_DIRECTORY_ENTRY_SECURITY
        offset, size = struct.unpack_from('<2I', data, sec)
        if not size:
            return False
        struct.pack_into('<2I', data, sec, 0, 0)
        if offset + size == len(data):
            del data[offset:]
        f.seek(0)
        f.write(data)
        f.truncate()
        return True


def read_ico(path):
    data = open(path, 'rb').read()
    _, kind, count = struct.unpack_from('<3H', data, 0)
    assert kind == 1
    entries = []
    for i in range(count):
        w, h, colors, res, planes, bits, size, offset = struct.unpack_from('<4B2H2I', data, 6 + 16 * i)
        entries.append(((w, h, colors, res, planes, bits, size), data[offset:offset + size]))
    return entries


def group_icon(entries):
    out = struct.pack('<3H', 0, 1, len(entries))
    for i, ((w, h, colors, res, planes, bits, size), _) in enumerate(entries, 1):
        out += struct.pack('<4B2HIH', w, h, colors, res, planes, bits, size, i)
    return out


def _pad(b):
    return b + b'\0' * (-len(b) % 4)


def _block(key, value=b'', children=b'', value_len=None, text=False):
    head_key = (key + '\0').encode('utf-16-le')
    body = _pad(struct.pack('<3H', 0, 0, 0) + head_key)
    if value:
        body = _pad(body + value)
    body += children
    length = len(body)
    if value_len is None:
        value_len = len(value) // 2 if text else len(value)
    return _pad(struct.pack('<3H', length, value_len, 1 if text else 0) + body[6:])


def _string(key, value):
    v = (value + '\0').encode('utf-16-le')
    return _block(key, v, text=True)


def version_info(version):
    parts = [int(x) for x in version.lstrip('v').split('.')] + [0, 0, 0, 0]
    ms, ls = (parts[0] << 16) | parts[1], (parts[2] << 16) | parts[3]
    fixed = struct.pack('<13I', 0xFEEF04BD, 0x00010000, ms, ls, ms, ls, 0x3F, 0, 0x00040004, 1, 0, 0, 0)
    text = version.lstrip('v')
    strings = b''.join(_string(k, v) for k, v in [
        ('CompanyName', NAME), ('FileDescription', NAME), ('FileVersion', text),
        ('InternalName', NAME), ('OriginalFilename', NAME + '.exe'),
        ('ProductName', NAME), ('ProductVersion', text)])
    table = _block(f'{LANG:04x}{CODEPAGE:04x}', children=strings, text=True)
    sfi = _block('StringFileInfo', children=table, text=True)
    var = _block('Translation', struct.pack('<2H', LANG, CODEPAGE))
    vfi = _block('VarFileInfo', children=var, text=True)
    return _block('VS_VERSION_INFO', fixed, children=sfi + vfi)


def existing(path, kinds):
    found = []
    h = win32api.LoadLibraryEx(path, 0, win32con.LOAD_LIBRARY_AS_DATAFILE)
    try:
        for kind in kinds:
            try:
                names = win32api.EnumResourceNames(h, kind)
            except win32api.error:
                continue
            for name in names:
                for lang in win32api.EnumResourceLanguages(h, kind, name):
                    found.append((kind, name, lang))
    finally:
        win32api.FreeLibrary(h)
    return found


def main():
    sys.path.insert(0, ROOT)
    from config import version
    shutil.copyfile(SRC, DST)
    print('签名已摘掉' if strip_signature(DST) else '原文件没签名')
    old = existing(DST, [win32con.RT_ICON, win32con.RT_GROUP_ICON, win32con.RT_VERSION])
    entries = read_ico(ICO)
    h = win32api.BeginUpdateResource(DST, False)
    try:
        for kind, name, lang in old:
            win32api.UpdateResource(h, kind, name, None, lang)
        for i, (_, image) in enumerate(entries, 1):
            win32api.UpdateResource(h, win32con.RT_ICON, i, image, LANG)
        win32api.UpdateResource(h, win32con.RT_GROUP_ICON, 1, group_icon(entries), LANG)
        win32api.UpdateResource(h, win32con.RT_VERSION, 1, version_info(version), LANG)
    except Exception:
        win32api.EndUpdateResource(h, True)
        raise
    win32api.EndUpdateResource(h, False)
    print('写好', DST, f'(删了 {len(old)} 个旧资源, 图标 {len(entries)} 种尺寸, 版本 {version})')


if __name__ == '__main__':
    main()
