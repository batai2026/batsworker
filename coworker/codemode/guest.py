"""guest runner：随 ``python -c`` 进入容器的自包含源码（仅 stdlib）。

协议（stdio JSON-lines，每行一个 JSON 对象）：
- 首行宿主 → guest：``{"__code__": "<模型代码>"}``
- guest → 宿主：``{"__call__": {"name": ..., "args": {...}}}``，随后读一行响应
  ``{"ok": bool, "result": ... | "error": ...}``
- 末尾 guest → 宿主（唯一一行）：``{"__result__": {"stdout": ..., "error": ...}}``

用户 ``print`` 被 ``redirect_stdout`` 捕获进 ``__result__.stdout``；协议行写原始
``sys.stdout``（重定向前捕获），两者严格隔离。
"""

from __future__ import annotations

_RUNNER = r'''
import contextlib
import io
import json
import sys
import traceback

_out = sys.stdout  # 协议通道：重定向前捕获，用户 print 污染不到


def _send(obj):
    _out.write(json.dumps(obj) + "\n")
    _out.flush()


def _recv():
    return json.loads(sys.stdin.readline())


class _Tools:
    """tools.<name>(**kwargs) → 桥接调用；宿主返回 ok=false 时抛异常让模型自修。"""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(**kwargs):
            _send({"__call__": {"name": name, "args": kwargs}})
            resp = _recv()
            if resp.get("ok"):
                return resp.get("result")
            raise RuntimeError(str(resp.get("error", "tool error")))

        return call


def main():
    code = _recv()["__code__"]
    buf = io.StringIO()
    err = None
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(code, "<model>", "exec"), {"tools": _Tools()})
    except Exception:
        err = traceback.format_exc()
    _send({"__result__": {"stdout": buf.getvalue(), "error": err}})


main()
'''


def guest_source() -> str:
    """返回注入容器的 runner 源码（``python -c`` 的参数）。"""
    return _RUNNER
