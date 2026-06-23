---
name: comment-coverage
description: 统计本项目 src 目录的注释覆盖率并按验收标准判定成败。当用户要检查/验收注释覆盖率、docstring 覆盖率、行内注释比例，或问"注释够不够""能不能过注释门禁"时使用。
---

# 注释覆盖率验收

调用 `scripts/comment_coverage.py` 统计 `src/` 的 docstring 与行内注释覆盖率，并据**验收标准**判定通过与否。

## 验收标准（全部满足才算成功）

| 指标 | 要求 |
|------|------|
| 模块 module docstring 覆盖率 | = 100% |
| 类 class docstring 覆盖率 | = 100% |
| 函数 function docstring 覆盖率 | = 100% |
| 行内注释比例（注释行 / 代码行） | > 15% |

## 执行步骤

1. 在项目根目录运行（`-X utf8` 避免 Windows 控制台中文乱码）：

   ```bash
   python -X utf8 scripts/comment_coverage.py \
     --fail-under-module 100 \
     --fail-under-class 100 \
     --fail-under-func 100 \
     --fail-under-inline 15 \
     --worst 12
   ```

2. 看**退出码**判定结果：
   - 退出码 `0` → **验收通过**，四项指标全部达标。
   - 退出码 `1` → **验收未通过**，stderr 会逐条打印 `[FAIL] ...`，指出哪个指标差多少。
   - 退出码 `2` → 参数错误或 `src/` 不存在。

3. 向用户汇报：
   - 先给结论（通过 / 未通过）。
   - 列出报告里的分层覆盖率数字（模块 / 类 / 函数 / 行内）。
   - 若未通过，结合 `--worst` 列出的"覆盖最低文件"，指出优先补哪些文件的 docstring 或行内注释。

## 说明

- 只做统计与判定，**不要**自动修改源码补注释，除非用户明确要求。
- 仅看报告不卡门禁时，去掉 `--fail-under-*` 参数即可：`python -X utf8 scripts/comment_coverage.py --worst 12`。
- 脚本统计口径与更多参数见 `scripts/comment_coverage.py` 顶部文档字符串。
