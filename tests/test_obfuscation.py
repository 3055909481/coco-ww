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

    def test_lambda_param_shadows_outer_local(self):
        """回归：外层推导变量与 lambda 参数同名（v1.0.6/v1.0.7 两次 NameError 的场景）。

        现在靠"根本不改名"保证正确；保留此测试防止有人把局部重命名加回来。
        """
        src = ("class C:\n"
               "    def __init__(self, last, index):\n"
               "        self.last = last\n"
               "        self.index = index\n"
               "def pick(chars):\n"
               "    chars = [char for char in chars if char is not None]\n"
               "    return min(chars, key=lambda char: (char.last, char.index))\n")
        call = "pick([C(2, 1), C(1, 2)]).last"
        self.assertEqual(*self._run_pair(src, call))

    def test_reassigned_parameter_not_renamed(self):
        """回归：函数体对参数重新赋值时参数名不能改，否则关键字调用失败。"""
        src = ("def f(items, limit=10):\n"
               "    items = list(items)[:limit]\n"
               "    total = len(items)\n"
               "    return total\n")
        self.assertEqual(*self._run_pair(src, "f(items=[1, 2, 3], limit=2)"))

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

    def _identifier_names(self, tree):
        """收集 AST 中出现的所有标识符（变量/参数/函数/类/属性名）。"""
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.alias):
                if node.asname:
                    names.add(node.asname)
        return names

    def test_all_real_sources_compile(self):
        files = list((ROOT / "src").rglob("*.py")) + list((ROOT / "pyappify").rglob("*.py"))
        self.assertGreater(len(files), 50)
        for path in files:
            with self.subTest(path=path):
                source = path.read_text(encoding="utf-8")
                obfuscated = obfuscate_source(source, KEY)
                compile(obfuscated, str(path), "exec")

    def test_no_identifier_is_ever_renamed(self):
        """最强保证：混淆绝不改名任何标识符，从根上杜绝 _obfN NameError。

        对全部真实源码验证：混淆后
        1) 不含任何 _obfN 形式的名字；
        2) 原始标识符集合是混淆后集合的子集（一个都没丢、没改）。
        这样 lambda/推导式/闭包同名变量永远不可能再被污染。
        """
        import re
        files = list((ROOT / "src").rglob("*.py")) + list((ROOT / "pyappify").rglob("*.py"))
        self.assertGreater(len(files), 50)
        obf_pattern = re.compile(r"^_obf\d+$")
        for path in files:
            with self.subTest(path=path):
                source = path.read_text(encoding="utf-8")
                original_names = self._identifier_names(ast.parse(source))
                obfuscated = obfuscate_source(source, KEY)
                result_names = self._identifier_names(ast.parse(obfuscated))
                self.assertFalse([n for n in result_names if obf_pattern.match(n)],
                                 f"{path}: 出现 _obfN 改名")
                self.assertTrue(original_names <= result_names,
                                f"{path}: 标识符丢失: {sorted(original_names - result_names)[:10]}")

    def test_historical_nameerror_cases(self):
        """v1.0.6 / v1.0.7 两次实机 NameError 的真实代码形状，永久回归。"""
        class Unit:
            def __init__(self, last, index, buff):
                self.last_switch_in_time = last
                self.index = index
                self.buff_time = buff
                self.is_main_dps = False

        # v1.0.6: BaseCombatTask._oldest_switch_target
        src1 = ("def oldest(candidates):\n"
                "    chars = [char for char in candidates if char is not None]\n"
                "    if not chars:\n"
                "        return None\n"
                "    return min(chars, key=lambda char: (char.last_switch_in_time, char.index))\n")
        # v1.0.7: BaseCombatTask._lowest_buff_remaining_target
        src2 = ("class Selector:\n"
                "    def buff_remaining(self, char):\n"
                "        return char.buff_time\n"
                "    def lowest(self, candidates):\n"
                "        buffers = [char for char in candidates if not char.is_main_dps and char.buff_time > 0]\n"
                "        if not buffers:\n"
                "            return None\n"
                "        return min(buffers, key=lambda char: (self.buff_remaining(char), char.last_switch_in_time, char.index))\n")
        items = [Unit(5, 1, 2.0), Unit(1, 2, 8.0)]
        for label, src, call in (
                ("v1.0.6-oldest", src1, lambda ns: ns["oldest"](items).index),
                ("v1.0.7-lowest", src2, lambda ns: ns["Selector"]().lowest(items).index)):
            with self.subTest(case=label):
                o = {}
                exec(compile(src, "<o>", "exec"), o)
                b = {}
                exec(compile(obfuscate_source(src, KEY), "<b>", "exec"), b)
                self.assertEqual(call(o), call(b))


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
        launcher = ROOT / "launcher" / "coco-ww.exe"
        if launcher.is_file():
            # 有启动器时：正常入口转发到 exe（它自己提 UAC），调试入口仍直连 python
            self.assertIn("coco-ww.exe", normal)
            self.assertIn("main_debug.pyc", debug)
            self.assertTrue((self.out / "coco-ww.exe").is_file())
        else:
            self.assertIn("-X utf8 main.pyc", normal)
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
        # 回归：清单里不能有非 ASCII 路径（docs/中文.md、中文名 bat），
        # 否则 jsdelivr 等 CDN 下载会 404/403，导致"更新失败/哈希校验不过"
        non_ascii = [p for p in manifest["files"] if any(ord(ch) > 127 for ch in p)]
        self.assertEqual(non_ascii, [], f"清单含非 ASCII 路径: {non_ascii}")
        self.assertFalse(any(p.startswith("docs/") for p in manifest["files"]))
        self.assertFalse(any(p.endswith(".bat") for p in manifest["files"]))
        for rel in ("main.pyc", "src/char/Aalto.pyc"):
            digest = hashlib.sha256((self.out / rel).read_bytes()).hexdigest()
            self.assertEqual(manifest["files"][rel]["sha256"], digest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
