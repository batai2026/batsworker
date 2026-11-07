# OpenShell prefix 接线（占位符展开）

## 背景

`sandbox_openshell_prefix`（config.toml 的 JSON argv 列表）现在是**静态命令前缀**：
执行时只是把命令追加到末尾。但 `sandbox_image` / `sandbox_network` / 工作区挂载这三个
配置项在 OpenShell 后端下**从未进入 argv**——`OpenShellExecutor` 只保存了 `image` /
`network` 字段，文档却说「工作区读写挂载、网络隔离、只读根由 prefix 拥有者的 CLI flags
表达」。站点部署想把真实 CLI flags 接上时，prefix 里没有任何办法引用这些配置值。

## 目标

prefix 中的每个 argv token 支持三个占位符，执行前展开：

| 占位符 | 取值 |
|---|---|
| `{workspace}` | 构造时解析后的工作区绝对路径 |
| `{image}` | `sandbox_image`（默认 `python:3.12-slim`） |
| `{network}` | `sandbox_network`（默认 `none`） |

示例（站点真实 flags 写进 prefix，而不是写死在代码里）：

```json
["openshell","exec","--mount","{workspace}:/workspace","--image","{image}","--network","{network}","--"]
```

## 约束

- **向后兼容**：不含占位符的 prefix 行为与现在完全一致（默认 `["openshell","exec","--"]`）。
- 只做字面替换，不引入 `str.format` 语义：未知花括号（如 `{gpu}`）保持原样，不报错。
- 占位符可以出现在任意 token 内部（如 `--mount={workspace}:/workspace`、`--net={network}`）。
- 可用性探测（`<argv[0]> --version`）使用展开后的第一个 token。
- `GET /v1/settings/openshell/status` 继续返回**原始 prefix**（用户配置的意图），不返回
  展开值（避免把本机路径写进状态响应）。
- 不猜测 NVIDIA OpenShell 的真实 CLI flags：本项只让 prefix 具备接线能力。

## 接口

`coworker/tools/openshell.py` 新增：

```python
def expand_prefix(prefix: list[str], *, workspace: str, image: str, network: str) -> list[str]:
    """按 {workspace} / {image} / {network} 展开每个 argv token；其它花括号原样保留。"""
```

`OpenShellExecutor` 在构造时保存 `self.prefix`（原始）与 `self.argv_prefix`（展开后），
`run()` 与 `_available()` 一律使用 `argv_prefix`。

## 验证

- `tests/test_openshell.py` 追加：
  - `expand_prefix` 纯函数：三占位符替换、token 内部替换、未知花括号保留、无占位符恒等；
  - fake CLI 捕获 argv：命令前缀展开为工作区/镜像/网络，命令本身仍在末尾；
  - 默认 prefix 下 argv 不变（回归保护）。
- `ruff check coworker tests`；全量回归保持 2166 passed / 9 skipped 基线。

## 非目标

- 不实现 OpenShell 的镜像拉取、容器生命周期、后台任务或文件工具路由（仍沿用
  LocalExecutor 回退契约）。
- 不改 GUI 设置卡片字段。
