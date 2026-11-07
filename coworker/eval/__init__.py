"""录制轨迹驱动的「模型 × harness」离线评测（建议路线 P1-4）。

harness = 一组能力槽（catalog id）+ 系统提示 + 权限模式。同一段录制输入在不同
(model, harness) 组合下重跑，用轨迹账本里的 model_request 摘要（digest/bytes）做横向比较。

P2-11 起新增 capture：真实会话一键导出为评测输入包（messages.jsonl + baseline.json）。
"""

__all__ = [
    "HARNESSES",
    "HarnessSpec",
    "RecordedTurn",
    "capture_case",
    "compare_baseline",
    "load_case",
    "main",
    "recorded_turns",
    "run_matrix",
]

_CAPTURE_NAMES = {"capture", "capture_case", "load_case"}


def __getattr__(name: str):
    # 惰性导出：`python -m coworker.eval.matrix` 会先导入本包，早导入子模块会让
    # runpy 发现模块已在 sys.modules 里并发出警告。这里用 import_module 而不是
    # `from . import matrix`，后者会先 hasattr 本包而再次触发 __getattr__ 造成递归。
    import importlib

    if name in _CAPTURE_NAMES:
        module = importlib.import_module(f"{__name__}.capture")
        if name == "capture":
            return module
        return getattr(module, name)
    module = importlib.import_module(f"{__name__}.matrix")
    if name == "matrix":
        return module
    return getattr(module, name)
