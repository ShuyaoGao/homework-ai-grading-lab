"""本机演示服务的生命周期：记录 uvicorn Server 实例，供“关闭演示”按钮和 停止演示.bat 优雅退出。

run.py --demo 用 uvicorn.Server 启动时会把实例登记到这里；用其他方式启动（如直接 uvicorn 命令）时没有实例，
关闭请求会在短暂延迟后结束本进程，保证不留后台进程。
"""
from __future__ import annotations

import os
import threading
from typing import Any

server: Any = None  # uvicorn.Server


def request_shutdown(delay: float = 0.5) -> None:
    """先让当前请求的响应发出去，再退出。"""

    def _stop() -> None:
        if server is not None:
            server.should_exit = True
            # 浏览器的长连接可能让优雅退出等很久；兜底强制结束
            # （守护线程：正常退出时不会拖住进程）
            killer = threading.Timer(8.0, os._exit, args=(0,))
            killer.daemon = True
            killer.start()
        else:
            os._exit(0)

    t = threading.Timer(delay, _stop)
    t.daemon = True
    t.start()
