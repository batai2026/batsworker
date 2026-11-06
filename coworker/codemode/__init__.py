"""Code mode（形态 B）：模型直出 Python 代码，沙箱执行，工具经审批门桥接。

- guest 在 Docker 容器内运行（``--network none``），与宿主经 stdio JSON-lines 通信；
- 无 DockerExecutor → fail-closed（``sandbox_unavailable``），宿主永不裸跑模型代码；
- 桥接的每次工具调用照常过该会话的 ``Approver``，单次 exec ≤ ``MAX_CALLS`` 次。
"""

from .bridge import MAX_CALLS, CodeModeRuntime, run_guest
from .exec_tool import exec_tool
from .guest import guest_source

__all__ = [
    "MAX_CALLS",
    "CodeModeRuntime",
    "exec_tool",
    "guest_source",
    "run_guest",
]
