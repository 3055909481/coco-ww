"""源码 AST 混淆变换核心（纯标准库，供 obfuscate_build.py 和测试使用）。

设计目标：在不改变程序行为的前提下，抹掉注释/docstring、编码长字符串常量，
并把源码经 ast.unparse 重排后编译成 .pyc 分发，让 release 里没有明文源码。

保守策略（功能正确优先，绝不改名字）：
- **不重命名任何局部变量/参数**。这是刻意的设计决策，见下方说明。
- 不碰模块级名、类名、函数/方法名、属性名、导入名。
- 字符串只编码"数据性"长字符串：跳过 dict key、比较运算对象、下标键、f-string 片段、set 元素、
  标识符样式串、路径样式串。这样编码后解码还原，行为完全一致。

═══════════════════════════════════════════════════════════════════════════
为什么不重命名局部变量（重要，不要"优化"回来）
═══════════════════════════════════════════════════════════════════════════
曾经实现过 AST 级局部变量重命名，但连续两次引发生产环境 NameError：

  v1.0.6: chars = [char for char in chars ...]; min(chars, key=lambda char: ...)
          → lambda 体里的 char 被外层同名推导变量污染 → _obf62 未定义
  v1.0.7: buffers = [char for char in candidates ...]; lambda char: ...
          → self._buff_remaining(char) 被推导变量污染 → _obf70 未定义

根因是 Python 的作用域规则极其微妙：推导式（ListComp/SetComp/DictComp/GeneratorExp）
在 Python 3 是独立作用域，lambda/嵌套函数又各是一层，闭包 free 变量、global/nonlocal、
walrus、match 绑定等都是独立的绑定来源。任何手写的 AST 作用域跟踪都难以穷尽这些边界，
修补一个案例就会漏掉下一个。

同时，局部变量重名的**防护收益几乎为零**：pyc 的 co_varnames 本来就明文保存所有变量名，
任何反编译器都能直接读出；把 char 改成 _obf2 只是把名字变丑，挡不住逆向。
真正有效的防护是：不分发明文 .py、去掉 docstring/注释、编码字符串常量、AST 重排——
这些都完整保留。

结论：正确性 > 边际混淆收益。此模块永久不做任何标识符改名。
"""

import ast
import base64
import re

DECODE_FUNC = "__coco_ww_dec__"
B64_MODULE = "__coco_ww_b64__"

_IDENTIFIER_LIKE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def encode_string(value: str, key: bytes) -> str:
    """把字符串编码成解码函数可接受的字面量（XOR + base64）。"""
    data = value.encode("utf-8")
    encoded = bytes(byte ^ key[i % len(key)] for i, byte in enumerate(data))
    return base64.b64encode(encoded).decode("ascii")


def decode_helper_source(key: bytes) -> str:
    """注入到每个含编码字符串的模块顶部的解码函数源码。"""
    key_literal = repr(key)
    return (
        f"import base64 as {B64_MODULE}\n"
        f"def {DECODE_FUNC}(_s, _k={key_literal}):\n"
        f"    return bytes(_c ^ _k[_i % len(_k)] for _i, _c in "
        f"enumerate({B64_MODULE}.b64decode(_s))).decode('utf-8')\n"
    )


class _DocstringStripper(ast.NodeTransformer):
    def _strip(self, node):
        if (node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            node.body = node.body[1:]
        self.generic_visit(node)
        return node

    visit_Module = _strip
    visit_ClassDef = _strip
    visit_FunctionDef = _strip
    visit_AsyncFunctionDef = _strip


class _StringEncoder(ast.NodeTransformer):
    """只编码"数据性"字符串，上下文不安全的跳过。"""

    def __init__(self, key: bytes, min_len: int = 10):
        self.key = key
        self.min_len = min_len
        self.unsafe = set()
        self.encoded = 0

    def _mark(self, node):
        # 字典里的 ** 展开元素其 key 为 None（Python 3.12+），直接跳过
        if node is None:
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            self.unsafe.add(id(node))
        for child in ast.iter_child_nodes(node):
            self._mark(child)

    def visit_Dict(self, node):
        for key in node.keys:
            self._mark(key)  # None 表示 ** 展开，_mark 内部已跳过
        for value in node.values:
            self.visit(value)
        return node

    def visit_Compare(self, node):
        self._mark(node.left)
        for comparator in node.comparators:
            self._mark(comparator)
        return node

    def visit_Subscript(self, node):
        self._mark(node.slice)
        return node

    def visit_JoinedStr(self, node):
        # f-string 片段必须保持 Constant，替换成 Call 会破坏 BUILD_STRING
        self._mark(node)
        return node

    def visit_Set(self, node):
        for element in node.elts:
            self._mark(element)
        return node

    def visit_Constant(self, node):
        if not isinstance(node.value, str) or id(node) in self.unsafe:
            return node
        value = node.value
        if (len(value) < self.min_len
                or _IDENTIFIER_LIKE.match(value)
                or "/" in value or "\\" in value
                or value.startswith("__") and value.endswith("__")):
            return node
        self.encoded += 1
        return ast.Call(
            func=ast.Name(id=DECODE_FUNC, ctx=ast.Load()),
            args=[ast.Constant(value=encode_string(value, self.key))],
            keywords=[],
        )


def obfuscate_source(source: str, key: bytes, *,
                     strip_docstrings: bool = True, encode_strings: bool = True) -> str:
    """把一份源码混淆后输出为新的源码字符串。

    注意：刻意不提供"重命名局部变量"选项，任何标识符都不会被改名。
    详见模块文档顶部的决策说明。
    """
    tree = ast.parse(source)
    if strip_docstrings:
        tree = _DocstringStripper().visit(tree)
    if encode_strings:
        encoder = _StringEncoder(key)
        tree = encoder.visit(tree)
        if encoder.encoded:
            helper_header = decode_helper_source(key)
        else:
            helper_header = ""
    else:
        helper_header = ""
    ast.fix_missing_locations(tree)
    return helper_header + ast.unparse(tree) + "\n"
