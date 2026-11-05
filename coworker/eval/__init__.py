"""录制轨迹驱动的「模型 × harness」离线评测（建议路线 P1-4）。

harness = 一组能力槽（catalog id）+ 系统提示 + 权限模式。同一段录制输入在不同
(model, harness) 组合下重跑，用轨迹账本里的 model_request 摘要（digest/bytes）做横向比较。
"""

__all__ = [
    "HARNESSES",
    "HarnessSpec",
    "RecordedTurn",
    "main",
    "recorded_turns",
    "run_matrix",
]


def __getattr__(name: str):
    # 惰性导出：`python -m coworker.eval.matrix` 会先导入本包，早导入子模块会让
    # runpy 发现模块已在 sys.modules 里并发出警告。这里用 import_module 而不是
    # `from . import matrix`，后者会先 hasattr 本包而再次触发 __getattr__ 造成递归。
    import importlib

    module = importlib.import_module(f"{__name__}.matrix")
    if name == "matrix":
        return module
    return getattr(module, name)

