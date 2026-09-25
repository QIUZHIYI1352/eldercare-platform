"""静态一致性：模块里用到的全局名必须真的被绑定。

这条检查是为一次具体事故加的：`backend/vision/monitor_session.py` 在
`_run_impl` 里调用 `VideoSource(...)` 却**没有 import**。当时 202 个测试全绿——
因为这段代码跑在后台线程里，而且异常被 `_run()` 的兜底 except 写进
`state["error"]`，只有用户点「开始监控」时才会看到一句
「识别出错：name 'VideoSource' is not defined」。

一个 NameError 能在全绿测试下活到用户手上，说明缺的不是某个测试，
而是**一类**检查：用到的全局名到底有没有被绑定。这里用 AST 做一个
pyflakes-lite（不引入第三方依赖），实跑全仓库 0 误报。

刻意做宽：按**整个文件**收集绑定名，不做词法作用域分析。因此
「函数 A 里绑定、函数 B 里使用」这种真错误抓不到；换来的是零误报——
一条会误报的检查很快会被一堆 ignore 加到形同虚设。
"""
import ast
import builtins
import os

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 不扫的目录：版本库/缓存/与本项目无关的一次性脚手架脚本
_SKIP_DIRS = {".git", ".workbuddy", "__pycache__", "node_modules",
              ".mypy_cache", ".pytest_cache", ".venv", "venv", "env"}

# 解释器/导入机制注入的名字，不是代码里绑定的
_PRELUDE = {"__name__", "__file__", "__doc__", "__package__", "__spec__",
            "__loader__", "__builtins__", "__path__", "__debug__",
            "WindowsError"}


def _rel_files():
    out = []
    for root, dirs, files in os.walk(_ROOT):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for f in sorted(files):
            if f.endswith(".py"):
                rel = os.path.relpath(os.path.join(root, f), _ROOT)
                out.append(rel.replace(os.sep, "/"))
    return out


_FILES = _rel_files()


def _arg_names(args):
    out = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    if args.vararg:
        out.add(args.vararg.arg)
    if args.kwarg:
        out.add(args.kwarg.arg)
    return out


def _bound_names(tree):
    """树里所有「绑定」：赋值/定义/import/形参/except as/global 声明。"""
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            if isinstance(n.ctx, (ast.Store, ast.Del)):
                names.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            (names.add(n.name), names.update(_arg_names(n.args)))
        elif isinstance(n, ast.ClassDef):
            names.add(n.name)
        elif isinstance(n, ast.Lambda):
            names.update(_arg_names(n.args))
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            names.update(n.names)
    return names


def _loaded_names(tree):
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _star_imports(tree):
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom)
            and any(a.name == "*" for a in n.names)]


def test_scan_covers_the_project():
    """防止检查被「意外扫不到文件」变成永远通过。"""
    assert len(_FILES) > 30, f"只扫到 {len(_FILES)} 个文件，扫描范围疑似失效"
    for need in ("backend/vision/monitor_session.py", "backend/main.py"):
        assert need in _FILES, f"{need} 不在扫描范围内"


def test_no_star_imports():
    """`from x import *` 会让上面的名字分析失效（无法知道绑定了什么）。

    与其静默跳过该文件（bug 就藏在被跳过的地方），不如直接禁止：
    本项目没有任何一处需要它。
    """
    bad = []
    for rel in _FILES:
        with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
            tree = ast.parse(f.read(), rel)
        if _star_imports(tree):
            bad.append(rel)
    assert not bad, f"以下文件使用了星号导入，请改成显式导入：{bad}"


@pytest.mark.parametrize("rel", _FILES)
def test_all_used_names_are_bound(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        tree = ast.parse(f.read(), rel)
    missing = sorted(_loaded_names(tree) - _bound_names(tree)
                     - set(dir(builtins)) - _PRELUDE)
    assert not missing, (
        f"{rel} 使用了没有绑定的名字 {missing}。"
        f"运行时就是 NameError，而且这类异常常被 try/except 兜底吞成一句"
        f"「识别出错：...」——测试全绿，用户先看见。"
        f"monitor_session.py 漏 import VideoSource 就是这么发生的。"
    )
