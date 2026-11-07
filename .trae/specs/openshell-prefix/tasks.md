# Tasks

1. [ ] `coworker/tools/openshell.py`：新增 `expand_prefix()`，构造时展开 `argv_prefix`
2. [ ] `run()` / `_available()` 改用展开后的 argv（默认 prefix 行为不变）
3. [ ] `tests/test_openshell.py` 追加占位符与 argv 断言
4. [ ] 文档：`docs/design/egress-and-openshell.md` 补占位符表与示例；`config.py` 注释同步
5. [ ] ruff + 目标用例
6. [ ] 全量回归 + 提交
