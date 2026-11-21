"""源码 AST 混淆变换核心（纯标准库，供 obfuscate_build.py 和测试使用）。

设计目标：在不改变程序行为的前提下，抹掉注释/docstring、打乱局部变量名、编码长字符串常量，
让 release 里分发的 .pyc 即使被反编译也难以阅读。

保守策略（功能正确优先）：
- 只重命名函数/方法/lambda 的参数与局部变量；不碰模块级名、类名、函数/方法名、属性名、导入名。
- 含 eval/exec/locals/globals/vars 调用的函数整体跳过重命名。
- global/nonlocal 声明的名字在全部作用域禁用重命名。
- 字符串只编码"数据性"长字符串：跳过 dict key、比较运算对象、下标键、f-string 片段、set 元素、
  标识符样式串、路径样式串。这样编码后解码还原，行为完全一致。

被混淆后的源码用 ast.unparse 输出（注释与格式全部丢失，本身就是一层混淆）。
"""

import ast
import base64
import re

DECODE_FUNC = "__coco_ww_dec__"
B64_MODULE = "__coco_ww_b64__"

_IDENTIFIER_LIKE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_FORBIDDEN_LOCAL_NAMES = {"__name__", "__file__", "__doc__", "__package__", "__class__", "__slots__"}
_DYNAMIC_CALLS = {"eval", "exec", "locals", "globals", "vars", "compile"}
# 保留：仅作文档说明——新名字现在由全局计数器生成，保证跨作用域唯一，
# 避免嵌套函数里同名临时变量遮蔽外层变量。


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


def _all_arg_nodes(arguments):
    """列出一个 arguments 节点里的所有 arg（含 posonly/args/kwonly/vararg/kwarg）。"""
    result = list(getattr(arguments, "posonlyargs", []))
    result += list(arguments.args)
    result += list(arguments.kwonlyargs)
    if arguments.vararg:
        result.append(arguments.vararg)
    if arguments.kwarg:
        result.append(arguments.kwarg)
    return result


def _arg_expressions(arguments):
    """arguments 里在定义时求值、属于外层作用域的表达式（默认值/注解）。"""
    exprs = list(arguments.defaults) + [d for d in arguments.kw_defaults if d]
    for arg in _all_arg_nodes(arguments):
        if arg.annotation:
            exprs.append(arg.annotation)
    return exprs


class _LocalRenamer(ast.NodeTransformer):
    """保守的函数内局部变量重命名。

    只重命名函数体内 Store/Del 的纯局部变量，不碰参数名——参数是公开 API，
    外部可能用关键字参数调用（如 RecentLogs(capacity=3)），改了会破坏调用方。
    每次进入函数用全局唯一计数器分配新名，避免嵌套作用域同名遮蔽。
    含 global/nonlocal 或 eval/exec/locals/globals/vars 的函数完全跳过重命名。
    """

    def __init__(self):
        self.counter = 0
        self.scope_stack = [_Scope()]  # 模块作用域：无绑定，作为查不到的终点

    def _new_name(self):
        self.counter += 1
        name = f"_obf{self.counter}"
        return name

    def visit_Name(self, node):
        # 从内到外找"绑定该名字"的作用域。关键：一旦某层绑定了名字（参数或局部变量），
        # 就只能用它自己的映射，即使该名字没被改名（如参数）也不能穿透到外层——
        # 否则 lambda 参数 char 会被外层的同名推导变量 _obf62 错误替换（NameError）。
        for scope in reversed(self.scope_stack):
            if node.id in scope.bound:
                node.id = scope.mapping.get(node.id, node.id)
                return node
        return node

    def _collect(self, body, arguments):
        """返回 (可改名集合, 是否含危险构造)。不递归进嵌套函数体。"""
        found = set()
        state = {"danger": False}

        class Collector(ast.NodeVisitor):
            def visit_FunctionDef(inner, node):
                for item in node.decorator_list:
                    self.visit(item)
                for expr in _arg_expressions(node.args):
                    self.visit(expr)
                if node.returns:
                    self.visit(node.returns)

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Lambda(inner, node):
                for expr in _arg_expressions(node.args):
                    self.visit(expr)

            def visit_ClassDef(inner, node):
                for item in node.decorator_list + node.bases:
                    self.visit(item)
                for kw in node.keywords:
                    self.visit(kw)

            def visit_Call(inner, node):
                if isinstance(node.func, ast.Name) and node.func.id in _DYNAMIC_CALLS:
                    state["danger"] = True
                self.generic_visit(node)

            def visit_Global(inner, node):
                state["danger"] = True

            def visit_Nonlocal(inner, node):
                state["danger"] = True

            def visit_Name(inner, node):
                if isinstance(node.ctx, (ast.Store, ast.Del)):
                    found.add(node.id)

        collector = Collector()
        # 参数名不加入 found：它们可能被外部以关键字参数调用，必须原样保留。
        for stmt in body:
            collector.visit(stmt)
        if state["danger"]:
            return set(), True
        safe = {n for n in found if n not in _FORBIDDEN_LOCAL_NAMES}
        return safe, False

    def _visit_function(self, node):
        # 定义时求值的表达式属于外层作用域，先处理
        node.decorator_list = [self.visit(d) for d in node.decorator_list]
        for expr in _arg_expressions(node.args):
            self.visit(expr)
        if node.returns:
            self.visit(node.returns)

        safe, danger = self._collect(node.body, node.args)
        param_names = {arg.arg for arg in _all_arg_nodes(node.args)}
        # 参数名绝不改名（外部可能用关键字参数调用），即使函数体里对它重新赋值。
        # 因此从待改名集合里剔除参数名；但它们仍属于本作用域绑定，挡住外层穿透。
        safe = safe - param_names
        scope = _Scope(bound=param_names | safe)
        if not danger:
            for name in sorted(safe):
                scope.mapping[name] = self._new_name()

        self.scope_stack.append(scope)
        node.body = [self.visit(stmt) for stmt in node.body]
        self.scope_stack.pop()
        return node

    def visit_FunctionDef(self, node):
        return self._visit_function(node)

    def visit_AsyncFunctionDef(self, node):
        return self._visit_function(node)

    def visit_Lambda(self, node):
        for expr in _arg_expressions(node.args):
            self.visit(expr)
        safe, danger = self._collect([node.body], node.args)
        param_names = {arg.arg for arg in _all_arg_nodes(node.args)}
        safe = safe - param_names  # lambda 参数也不改名
        scope = _Scope(bound=param_names | safe)
        if not danger:
            for name in sorted(safe):
                scope.mapping[name] = self._new_name()
        self.scope_stack.append(scope)
        node.body = self.visit(node.body)
        self.scope_stack.pop()
        return node

    def visit_ClassDef(self, node):
        # 类体不重命名类属性；但允许闭包引用外层已改名的局部变量。
        node.decorator_list = [self.visit(d) for d in node.decorator_list]
        node.bases = [self.visit(b) for b in node.bases]
        node.keywords = [self.visit(k) for k in node.keywords]
        node.body = [self.visit(stmt) for stmt in node.body]
        return node


class _Scope:
    """一个函数作用域。

    bound: 该作用域绑定的所有名字（参数 + 局部变量），含未改名的参数；
    mapping: 其中被改名的 原名 -> 唯一新名。
    visit_Name 只认最近的绑定层，未改名的绑定（参数）也必须挡住外层穿透。
    """

    __slots__ = ("mapping", "bound")

    def __init__(self, bound=None):
        self.mapping = {}
        self.bound = set(bound or ())

    def __contains__(self, name):
        return name in self.bound


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
        # 字典里的 **展开元素其 key 为 None（Python 3.12+），直接跳过
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


def obfuscate_source(source: str, key: bytes, *, rename_locals=True,
                     strip_docstrings=True, encode_strings=True) -> str:
    """把一份源码混淆后输出为新的源码字符串。"""
    tree = ast.parse(source)
    if strip_docstrings:
        tree = _DocstringStripper().visit(tree)
    if rename_locals:
        # nonlocal 跨越作用域绑定：任一处存在就整体跳过本模块的重命名。
        # 项目里仅 1 处（src/char/Linnai.py），正确性优先于混淆覆盖率。
        has_nonlocal = any(isinstance(n, (ast.Nonlocal,)) for n in ast.walk(tree))
        if not has_nonlocal:
            tree = _LocalRenamer().visit(tree)
    helper_header = ""
    if encode_strings:
        encoder = _StringEncoder(key)
        tree = encoder.visit(tree)
        if encoder.encoded:
            helper_header = decode_helper_source(key)
    ast.fix_missing_locations(tree)
    return helper_header + ast.unparse(tree) + "\n"
