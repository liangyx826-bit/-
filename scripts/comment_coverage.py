#!/usr/bin/env python3
"""docstring / 注释覆盖率扫描脚本（可作 CI 验收门禁）。

扫描 ``src/`` 下的所有 Python 文件，按 **模块 / 类 / 函数** 三个层级分别统计
docstring 覆盖率，并单独统计行内注释比例。相比"注释行 / 总行数"这种笼统口径，
分层结果能直接看出短板是"缺类 docstring"还是"缺行内注释"，便于针对性补全。

统计口径::

    docstring 覆盖率 = 有 docstring 的对象数 / 该层级对象总数
                       （对象 = 模块 / 类 / 函数；函数含方法、async 函数）
    行内注释比例     = 含 # 注释的物理行数 / 含代码的物理行数

退出码（便于 CI 拦截）::

    0  全部达标
    1  有任一被检查的指标低于阈值
    2  参数错误 / 根目录不存在

用法::

    python scripts/comment_coverage.py                       # 扫描 src/，仅报告不拦截
    python scripts/comment_coverage.py --fail-under 85        # 总体 docstring < 85% 则退出码 1
    python scripts/comment_coverage.py --fail-under-class 50  # 单独卡"类" docstring 覆盖率
    python scripts/comment_coverage.py --fail-under-inline 15 # 行内注释比例 < 15% 则退出码 1
    python scripts/comment_coverage.py --root path            # 指定根目录
    python scripts/comment_coverage.py --sort cov             # 文件按 docstring 覆盖率升序（先看最差）
    python scripts/comment_coverage.py --worst 10             # 只列 docstring 覆盖最低的 10 个文件
    python scripts/comment_coverage.py --out report.txt       # 报告另存为 UTF-8 文件

提示：Windows 控制台默认 GBK，输出中文可能报 UnicodeEncodeError，建议用
``python -X utf8 scripts/comment_coverage.py`` 运行，或加 ``--out`` 写入 UTF-8 文件。
"""

from __future__ import annotations

import argparse
import ast
import io
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FileStat:
    """单个文件的统计结果。"""

    path: Path
    total: int = 0            # 物理总行数
    # 各层级 docstring：(有 docstring 数, 对象总数)
    module: tuple[int, int] = (0, 0)
    cls: list[int] = field(default_factory=lambda: [0, 0])    # [有, 总]
    func: list[int] = field(default_factory=lambda: [0, 0])   # [有, 总]
    comment_lines: int = 0    # 含 # 注释的物理行数（含行尾注释）
    code_lines: int = 0       # 含代码的物理行数

    @property
    def obj_total(self) -> int:
        """该文件被统计的对象总数（模块 + 类 + 函数）。"""
        return self.module[1] + self.cls[1] + self.func[1]

    @property
    def obj_doc(self) -> int:
        """该文件有 docstring 的对象数。"""
        return self.module[0] + self.cls[0] + self.func[0]

    @property
    def coverage(self) -> float:
        """该文件整体 docstring 覆盖率，无对象时返回 1.0（视为满分不拖累排序）。"""
        return self.obj_doc / self.obj_total if self.obj_total else 1.0


def analyze(path: Path) -> FileStat | None:
    """分析单个文件，返回统计结果；读取或解析失败时返回 None 由调用方处理。"""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    src_lines = source.splitlines()
    stat = FileStat(path=path, total=len(src_lines))

    # 1) docstring：用 AST 精确判定模块 / 类 / 函数
    stat.module = (1 if ast.get_docstring(tree) else 0, 1)
    cls_doc = cls_tot = func_doc = func_tot = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            cls_tot += 1
            cls_doc += 1 if ast.get_docstring(node) else 0
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_tot += 1
            func_doc += 1 if ast.get_docstring(node) else 0
    stat.cls = [cls_doc, cls_tot]
    stat.func = [func_doc, func_tot]

    # 2) 行内注释 / 代码行：用 tokenize 按物理行去重统计
    comment_rows: set[int] = set()
    code_rows: set[int] = set()
    ignore = {
        tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
        tokenize.ENCODING, tokenize.ENDMARKER, tokenize.COMMENT,
    }
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                comment_rows.add(tok.start[0])
            elif tok.type == tokenize.STRING:
                # 字符串字面量（含 docstring）跨越的物理行不计入"代码行"，
                # 避免多行 docstring 把代码行基数抬高、稀释行内注释比例。
                continue
            elif tok.type not in ignore:
                code_rows.add(tok.start[0])
    except (tokenize.TokenError, IndentationError):
        pass
    stat.comment_lines = len(comment_rows)
    stat.code_lines = len(code_rows)
    return stat


def iter_py_files(root: Path):
    """遍历 root 下的 .py 文件，跳过 __pycache__ 等缓存目录。"""
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        yield p


def _rel(path: Path, root: Path) -> Path:
    """尽量返回相对 root 的路径，失败则返回原路径。"""
    try:
        return path.relative_to(root)
    except ValueError:
        return path


def _pct(part: int, whole: int) -> str:
    """百分比字符串，分母为 0 时返回 ``n/a``。"""
    return f"{100 * part / whole:5.1f}%" if whole else "  n/a"


def render(stats: list[FileStat], skipped: list[Path], root: Path,
           worst: int | None) -> tuple[str, dict[str, tuple[int, int]]]:
    """渲染报告文本，并返回各层级汇总 ``{层级: (有, 总)}`` 供门禁判定。"""
    out = io.StringIO()

    mod = (sum(s.module[0] for s in stats), sum(s.module[1] for s in stats))
    cls = (sum(s.cls[0] for s in stats), sum(s.cls[1] for s in stats))
    fnc = (sum(s.func[0] for s in stats), sum(s.func[1] for s in stats))
    tot = (mod[0] + cls[0] + fnc[0], mod[1] + cls[1] + fnc[1])
    cmt = sum(s.comment_lines for s in stats)
    code = sum(s.code_lines for s in stats)
    summary = {
        "module": mod, "class": cls, "function": fnc, "total": tot,
        "inline": (cmt, code),
    }

    out.write("== docstring 覆盖率 ==\n")
    out.write(f"  模块  module   : {mod[0]:>4}/{mod[1]:<4} {_pct(*mod)}\n")
    out.write(f"  类    class    : {cls[0]:>4}/{cls[1]:<4} {_pct(*cls)}\n")
    out.write(f"  函数  function : {fnc[0]:>4}/{fnc[1]:<4} {_pct(*fnc)}\n")
    out.write(f"  合计  total    : {tot[0]:>4}/{tot[1]:<4} {_pct(*tot)}\n")
    out.write("\n== 行内注释比例 ==\n")
    out.write(f"  注释行 {cmt} / 代码行 {code} = {_pct(cmt, code)}\n")

    # 文件清单：仅列出含可统计对象的文件
    listed = [s for s in stats if s.obj_total > 0]
    if worst is not None:
        listed = sorted(listed, key=lambda s: (s.coverage, -s.obj_total))[:worst]
        out.write(f"\n== docstring 覆盖最低的 {len(listed)} 个文件 ==\n")
    else:
        out.write(f"\n== 各文件明细（{len(listed)} 个）==\n")
    width = max([len(str(_rel(s.path, root))) for s in listed] + [len("FILE")])
    out.write(f"{'FILE':<{width}}  {'DOCSTR':>10} {'COV':>7}  {'CMT':>4}/{'CODE':<5}\n")
    out.write("-" * (width + 32) + "\n")
    for s in listed:
        doc = f"{s.obj_doc}/{s.obj_total}"
        out.write(f"{str(_rel(s.path, root)):<{width}}  {doc:>10} {s.coverage * 100:>6.1f}%"
                  f"  {s.comment_lines:>4}/{s.code_lines:<5}\n")

    if skipped:
        out.write(f"\n已跳过 {len(skipped)} 个文件（读取或语法解析失败）。\n")
    return out.getvalue(), summary


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        description="按模块/类/函数分层统计 docstring 覆盖率，可作 CI 验收门禁")
    parser.add_argument("--root", default="src", help="扫描根目录（默认 src）")
    parser.add_argument("--fail-under", type=float, default=None, metavar="PCT",
                        help="总体 docstring 覆盖率低于该百分比则退出码 1")
    parser.add_argument("--fail-under-module", type=float, default=None, metavar="PCT",
                        help="模块层 docstring 覆盖率门禁")
    parser.add_argument("--fail-under-class", type=float, default=None, metavar="PCT",
                        help="类层 docstring 覆盖率门禁")
    parser.add_argument("--fail-under-func", type=float, default=None, metavar="PCT",
                        help="函数层 docstring 覆盖率门禁")
    parser.add_argument("--fail-under-inline", type=float, default=None, metavar="PCT",
                        help="行内注释比例（注释行/代码行）低于该百分比则退出码 1")
    parser.add_argument("--sort", choices=("path", "cov"), default="path",
                        help="文件明细排序：path 按路径，cov 按覆盖率升序")
    parser.add_argument("--worst", type=int, default=None, metavar="N",
                        help="只列出 docstring 覆盖最低的 N 个文件")
    parser.add_argument("--out", default=None,
                        help="把报告写入指定文件（UTF-8），不指定则打印到终端")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"错误：根目录不存在 -> {root.resolve()}", file=sys.stderr)
        return 2

    stats: list[FileStat] = []
    skipped: list[Path] = []
    for path in iter_py_files(root):
        st = analyze(path)
        if st is None:
            skipped.append(path)
        else:
            stats.append(st)

    if not stats:
        print("没有可统计的文件。")
        return 0

    if args.sort == "cov":
        stats.sort(key=lambda s: (s.coverage, -s.obj_total))

    text, summary = render(stats, skipped, root, args.worst)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"报告已写入 {args.out}")
    else:
        # 兜底处理 Windows 控制台 GBK 编码，避免 UnicodeEncodeError
        try:
            sys.stdout.write(text)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(text.encode("utf-8", "replace"))

    # 门禁判定：任一被设置的阈值未达标即失败
    gates = [
        ("总体", args.fail_under, summary["total"]),
        ("模块", args.fail_under_module, summary["module"]),
        ("类", args.fail_under_class, summary["class"]),
        ("函数", args.fail_under_func, summary["function"]),
    ]
    failed = False
    for name, threshold, (doc, tot) in gates:
        if threshold is None:
            continue
        actual = 100 * doc / tot if tot else 100.0
        if actual + 1e-9 < threshold:
            print(f"[FAIL] {name} docstring 覆盖率 {actual:.1f}% < 阈值 {threshold:.1f}%",
                  file=sys.stderr)
            failed = True

    if args.fail_under_inline is not None:
        cmt, code = summary["inline"]
        actual = 100 * cmt / code if code else 0.0
        if actual + 1e-9 < args.fail_under_inline:
            print(f"[FAIL] 行内注释比例 {actual:.1f}% < 阈值 {args.fail_under_inline:.1f}%",
                  file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
