"""混淆构建链路的离线测试（不依赖游戏/Qt/cv2）。

在仓库根目录运行：
    python -X utf8 tests\\test_obfuscation.py
"""
import ast
import hashlib
import importlib.util
import json
import marshal
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from obfuscate import obfuscate_source  # noqa: E402
from _obf_common import seal_text, unseal_text  # noqa: E402

KEY = b"coco-source-obfuscation-key-v1"


class TestAstObfuscation(unittest.TestCase):
    def _run_pair(self, source, call):
        original = {}
        exec(compile(source, "<original>", "exec"), original)
        obfuscated = obfuscate_source(source, KEY)
        compile(obfuscated, "<obfuscated>", "exec")  # 语法必须有效
        rebuilt = {}
        exec(compile(obfuscated, "<obfuscated>", "exec"), rebuilt)
        return eval(call, original), eval(call, rebuilt)

    def test_local_variables(self):
        src = "def f(a, b=2):\n    total = a + b\n    tmp = total * 3\n    return tmp\n"
        self.assertEqual(*self._run_pair(src, "f(3, 4)"))

    def test_nested_closure(self):
        src = ("def outer(x):\n"
               "    base = x + 10\n"
               "    def inner(y):\n"
               "        more = y + base\n"
               "        return more\n"
               "    return inner(5), base\n")
        self.assertEqual(*self._run_pair(src, "outer(7)"))

    def test_keyword_arguments_preserved(self):
        src = ("class Box:\n"
               "    def __init__(self, capacity=5, level=2):\n"
               "        self.capacity = capacity\n"
               "        self.level = level\n")
        left, right = self._run_pair(src, "(Box(capacity=3, level=9).capacity, Box(capacity=3, level=9).level)")
        self.assertEqual(left, right)

    def test_global_statement(self):
        src = "_counter = 0\ndef bump():\n    global _counter\n    _counter += 1\n    return _counter\n"
        self.assertEqual(*self._run_pair(src, "(bump(), bump())"))

    def test_nonlocal_statement(self):
        src = ("def make():\n"
               "    count = 0\n"
               "    def inc():\n"
               "        nonlocal count\n"
               "        count += 1\n"
               "        return count\n"
               "    return inc\n")
        namespace = {}
        exec(compile(src, "<o>", "exec"), namespace)
        obf_ns = {}
        exec(compile(obfuscate_source(src, KEY), "<b>", "exec"), obf_ns)
        a, b = namespace["make"](), obf_ns["make"]()
        self.assertEqual((a(), a()), (b(), b()))

    def test_string_encoding_roundtrip(self):
        src = "def message():\n    text = '这是一段很长的中文字符串需要被编码处理'\n    return text\n"
        left, right = self._run_pair(src, "message()")
        self.assertEqual(left, right)
        self.assertEqual(left, "这是一段很长的中文字符串需要被编码处理")

    def test_dict_keys_not_encoded(self):
        src = ("def build():\n"
               "    data = {'a_very_long_dictionary_key_name': 'a long value here'}\n"
               "    return data['a_very_long_dictionary_key_name']\n")
        self.assertEqual(*self._run_pair(src, "build()"))

    def test_all_real_sources_compile(self):
        files = list((ROOT / "src").rglob("*.py")) + list((ROOT / "pyappify").rglob("*.py"))
        self.assertGreater(len(files), 50)
        for path in files:
            with self.subTest(path=path):
                source = path.read_text(encoding="utf-8")
                obfuscated = obfuscate_source(source, KEY)
                compile(obfuscated, str(path), "exec")


class TestSecureSource(unittest.TestCase):
    def test_seal_roundtrip(self):
        payload = {"Aalto": "class Aalto:\n    pass\n", "Linnai": "class Linnai:\n    x = 1\n"}
        self.assertEqual(unseal_text(seal_text(payload)), payload)

    def test_real_char_sources_sealed(self):
        sources = {}
        for path in (ROOT / "src" / "char").glob("*.py"):
            if path.name.startswith("_"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    sources[node.name] = path.read_text(encoding="utf-8")
        self.assertGreater(len(sources), 50)
        self.assertEqual(unseal_text(seal_text(sources)), sources)


class TestBuildArtifact(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = None
        for candidate in ("py -3.12", "python3.12"):
            parts = candidate.split()
            try:
                result = subprocess.run(parts + ["-c", "import sys;assert sys.version_info[:2]==(3,12)"],
                                        capture_output=True, timeout=20)
                if result.returncode == 0:
                    compiler = candidate
                    break
            except OSError:
                continue
        if not compiler:
            raise unittest.SkipTest("没有 Python 3.12，跳过产物测试")
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tempdir.name) / "release"
        script = ROOT / "tools" / "obfuscate_build.py"
        result = subprocess.run([sys.executable, "-X", "utf8", str(script),
                                 "--out", str(cls.out), "--python", compiler, "--quiet"],
                                capture_output=True, text=True, cwd=str(ROOT), timeout=300)
        if result.returncode != 0:
            raise AssertionError(f"构建失败:\n{result.stdout}\n{result.stderr}")

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def test_no_plain_source(self):
        leftovers = list((self.out / "src").rglob("*.py")) + list((self.out / "pyappify").rglob("*.py"))
        self.assertEqual(leftovers, [], f"产物残留明文源码: {leftovers}")
        self.assertFalse((self.out / "main.py").exists())
        self.assertTrue((self.out / "main.pyc").exists())

    def test_pyc_loadable_and_filename_correct(self):
        target = self.out / "src" / "char" / "Aalto.pyc"
        with open(target, "rb") as f:
            header = f.read(16)
            code = marshal.load(f)
        self.assertEqual(int.from_bytes(header[4:8], "little"), 1)  # hash-based
        self.assertTrue(code.co_filename.endswith("src/char/Aalto.py") or
                        code.co_filename.endswith("src\\char\\Aalto.py"))

    def test_builtin_data_exists(self):
        data = self.out / "src" / "_builtin_sources.dat"
        self.assertTrue(data.is_file())
        self.assertGreater(data.stat().st_size, 1000)

    def test_launchers_point_to_pyc(self):
        normal = (self.out / "启动 coco-ww.bat").read_text(encoding="utf-8")
        debug = (self.out / "调试启动(带控制台).bat").read_text(encoding="utf-8")
        self.assertIn("-X utf8 main.pyc", normal)
        self.assertNotIn("-X utf8 main.py\n", normal)  # 精确排除未改的入口
        self.assertIn("main_debug.pyc", debug)

    def test_version_file(self):
        version = (self.out / "version.txt").read_text(encoding="utf-8").strip()
        self.assertTrue(version.startswith("v"))

    def test_manifest_from_artifact(self):
        manifest_script = ROOT / "tools" / "release_manifest.py"
        out_dir = Path(self.tempdir.name) / "manifest"
        # --commit 用 orphan 分支式 SHA（与 main 无共同祖先），--source-commit 用 HEAD。
        # 这正是 CI 里 obfuscated 分支的场景：改坏的话 change_lines 的 git log 会崩。
        result = subprocess.run([sys.executable, "-X", "utf8", str(manifest_script),
                                 str(out_dir), "--from-dir", str(self.out),
                                 "--commit", "a" * 40,
                                 "--source-commit", "HEAD",
                                 "--prev-tag", "v1.0.4"],
                                capture_output=True, text=True, cwd=str(ROOT), timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((out_dir / "update-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["format"], 1)
        self.assertIn("main.pyc", manifest["files"])
        self.assertIn("src/_builtin_sources.dat", manifest["files"])
        self.assertNotIn("main.py", manifest["files"])
        for rel in ("main.pyc", "src/char/Aalto.pyc"):
            digest = hashlib.sha256((self.out / rel).read_bytes()).hexdigest()
            self.assertEqual(manifest["files"][rel]["sha256"], digest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
