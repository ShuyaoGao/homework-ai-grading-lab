"""一条命令启动：建库（可选写入演示数据）→ 启动 FastAPI（同时托管前端）。

    .venv\\Scripts\\python run.py --seed          # 首次运行：写入演示数据并启动
    .venv\\Scripts\\python run.py                 # 之后直接启动
    .venv\\Scripts\\python run.py --host 0.0.0.0 # 允许同一局域网的手机访问

本机一键演示（启动演示.bat / 停止演示.bat 调用的就是这两条）：

    .venv\\Scripts\\python run.py --demo          # 强制 mock、需要时写入演示数据、自动打开浏览器进入演示首页
    .venv\\Scripts\\python run.py --stop          # 关闭正在运行的演示服务（先优雅退出，不行再结束进程）
"""
from __future__ import annotations

import argparse
import ctypes
import json
import shutil
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_NAME = "demo_server.json"  # 放在数据目录里：{pid, port, url}


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 80))  # 不会真的发包，只用于选出本机局域网地址
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pick_port(preferred: int, host: str) -> int:
    for p in range(preferred, preferred + 20):
        if port_free(p, "0.0.0.0" if host == "0.0.0.0" else "127.0.0.1"):
            return p
    raise SystemExit(f"端口 {preferred}–{preferred + 19} 都被占用了，请用 --port 指定一个空闲端口")


# 状态文件和演示目录都以“基础数据目录”为准（EDU_DATA_DIR，默认 data/），启动时先记下来，
# 因为 --demo 之后会把 EDU_DATA_DIR 改指向演示专用目录。
BASE_DIR = Path(os.environ.get("EDU_DATA_DIR", str(ROOT / "data")))
DEMO_DIR_NAME = "demo"        # 演示服务实际使用的数据目录：每次启动都从快照恢复
SEED_DIR_NAME = "demo_seed"   # 种子快照：只在第一次生成，之后只读
DEMO_MARKER = ".edu_demo_data"  # 只有带这个标记文件的目录才允许被重置删除


def data_dir() -> Path:
    return BASE_DIR


def state_path() -> Path:
    return BASE_DIR / STATE_NAME


def _seed_snapshot(seed_dir: Path, images: bool) -> None:
    """在独立子进程里把种子数据写进快照目录（配置是进程级全局的，不在本进程里切换数据目录）。"""
    want = "images" if images else "no-images"
    marker = seed_dir / DEMO_MARKER
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == want:
        return
    _safe_rmtree(seed_dir)
    seed_dir.mkdir(parents=True)
    marker.write_text(want, encoding="utf-8")
    if images:
        print("第一次运行：生成演示种子数据（约 71 张手写作业图片，需要 1–2 分钟）…")
    env = {**os.environ, "EDU_DATA_DIR": str(seed_dir), "PYTHONIOENCODING": "utf-8",
           "OCR_PROVIDER": "mock", "GRADER_PROVIDER": "mock"}
    cmd = [sys.executable, "-m", "backend.seed"] + ([] if images else ["--no-images"])
    r = subprocess.run(cmd, cwd=ROOT, env=env)
    if r.returncode != 0:
        marker.write_text("failed", encoding="utf-8")  # 保留标记，下次识别为不完整并重建
        raise SystemExit("写入演示种子数据失败，见上面的报错")


def _safe_rmtree(d: Path) -> None:
    """只删除演示专用目录：必须是基础目录下的 demo / demo_seed，且（存在时）带标记文件。"""
    if not d.exists():
        return
    d = d.resolve()
    allowed = {(BASE_DIR / DEMO_DIR_NAME).resolve(), (BASE_DIR / SEED_DIR_NAME).resolve()}
    if d not in allowed or not (d / DEMO_MARKER).exists():
        raise SystemExit(f"拒绝删除 {d}：不是带标记的演示数据目录")
    shutil.rmtree(d)


def prepare_demo_data(images: bool = True) -> Path:
    """每次启动演示：把演示目录恢复成种子快照的样子。只动 data/demo 和 data/demo_seed，不碰 data/ 下的正式数据。"""
    seed_dir = BASE_DIR / SEED_DIR_NAME
    demo_dir = BASE_DIR / DEMO_DIR_NAME
    _seed_snapshot(seed_dir, images)
    _safe_rmtree(demo_dir)
    shutil.copytree(seed_dir, demo_dir)  # 快照里已经带标记文件
    return demo_dir


def read_state() -> dict | None:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def pid_alive(pid: int) -> bool:
    """Windows 上不能用 os.kill(pid, 0)（那会直接结束进程），改用 OpenProcess + GetExitCodeProcess。"""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def health(port: int, timeout: float = 1.5) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None


def stop_demo(wait: float = 12.0) -> int:
    """关闭演示服务：先请求 /api/demo/shutdown 优雅退出，超时再 taskkill。返回进程退出码（0 = 已关闭或本来就没开）。"""
    st = read_state()
    if not st:
        print("没有正在运行的演示服务。")
        return 0
    pid, port = int(st.get("pid", 0)), int(st.get("port", 0))
    if not pid_alive(pid):
        print("演示服务已经不在运行了，清理状态文件。")
        state_path().unlink(missing_ok=True)
        return 0
    print(f"正在关闭演示服务（进程 {pid}，端口 {port}）…")
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/demo/shutdown", method="POST",
                                     headers={"X-Demo-Action": "shutdown"})
        urllib.request.urlopen(req, timeout=3).read()
    except (OSError, urllib.error.URLError) as e:
        print(f"  优雅关闭请求失败（{e}），稍后直接结束进程")
    t0 = time.time()
    while time.time() - t0 < wait and pid_alive(pid):
        time.sleep(0.3)
    if pid_alive(pid):
        print("  服务没有按时退出，结束进程树…")
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        time.sleep(0.5)
    if pid_alive(pid):
        print("关闭失败，请在任务管理器里结束 python.exe，或关闭“启动演示”那个黑色窗口。")
        return 1
    state_path().unlink(missing_ok=True)
    print("演示服务已关闭，没有遗留后台进程。")
    return 0


def open_when_ready(port: int, url: str) -> None:
    def _run() -> None:
        for _ in range(240):
            if health(port, 0.5):
                webbrowser.open(url)
                return
            time.sleep(0.25)

    threading.Thread(target=_run, daemon=True).start()


def main() -> None:
    ap = argparse.ArgumentParser(description="启动教育评价 AI 系统")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址；手机访问请用 0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--seed", action="store_true", help="启动前写入演示数据（已存在则跳过）")
    ap.add_argument("--reset", action="store_true",
                    help="与 --seed 一起用：清空后重建演示数据；与 --demo 一起用：重新生成种子快照")
    ap.add_argument("--demo", action="store_true",
                    help="本机一键演示：强制 mock（不调用任何付费接口）、自动写入演示数据、打开浏览器进入演示首页")
    ap.add_argument("--no-browser", action="store_true", help="与 --demo 一起用：不自动打开浏览器")
    ap.add_argument("--no-images", action="store_true", help="写入演示数据时不生成图片（更快，仅用于测试）")
    ap.add_argument("--stop", action="store_true", help="关闭正在运行的演示服务")
    args = ap.parse_args()

    if args.stop:
        raise SystemExit(stop_demo())

    if args.demo and args.reset:
        _safe_rmtree(BASE_DIR / SEED_DIR_NAME)
    if args.demo:
        # 必须在导入 backend.config 之前设置：演示一律用离线 mock，哪怕 .env 里填了 key 也不会花钱
        os.environ["EDU_DEMO_MODE"] = "1"
        os.environ["OCR_PROVIDER"] = "mock"
        os.environ["GRADER_PROVIDER"] = "mock"
        st = read_state()
        if st and pid_alive(int(st.get("pid", 0))) and (h := health(int(st.get("port", 0)))) and h.get("demo"):
            url = f"http://127.0.0.1:{st['port']}/#/demo"
            print(f"演示服务已经在运行：{url}\n直接打开浏览器。要关闭请双击“停止演示.bat”。")
            if not args.no_browser:
                webbrowser.open(url)
            return
        args.port = pick_port(args.port, args.host)
        # 每次启动都恢复成种子状态，演示数据库与正式数据（data/edu_eval.db）完全分开
        demo_dir = prepare_demo_data(images=not args.no_images)
        os.environ["EDU_DATA_DIR"] = str(demo_dir)
        print(f"演示数据已恢复为初始状态：{demo_dir}")
        if args.host == "0.0.0.0":
            os.environ["EDU_DEMO_LAN_URL"] = f"http://{lan_ip()}:{args.port}/"

    import uvicorn

    from backend.config import get_settings

    s = get_settings()
    if args.seed:
        from backend.seed import seed

        if not args.no_images:
            print("检查演示数据（第一次运行会生成约 71 张手写作业图片，需要 1–2 分钟）…")
        print(seed(reset=args.reset, images=not args.no_images, quiet=True))
    print(f"数据目录：{s.data_dir}")
    print(f"OCR provider：{s.resolve_ocr_provider()}；评分 provider：{s.resolve_grader_provider()}")
    print(f"本机访问： http://127.0.0.1:{args.port}/    API 文档： http://127.0.0.1:{args.port}/api/docs")
    if args.host == "0.0.0.0":
        print(f"手机访问（同一局域网）： http://{lan_ip()}:{args.port}/")

    config = uvicorn.Config("backend.main:app", host=args.host, port=args.port, log_level="info")
    server = uvicorn.Server(config)
    if not args.demo:
        server.run()
        return

    from backend.demo import lifecycle

    lifecycle.server = server
    url = f"http://127.0.0.1:{args.port}/#/demo"
    state_path().write_text(json.dumps({"pid": os.getpid(), "port": args.port, "url": url}), encoding="utf-8")
    print("\n================ 演示模式 ================")
    print(f"演示首页： {url}")
    print("关闭方法：页面右上角“关闭演示”，或双击“停止演示.bat”，或直接关掉这个窗口。")
    print("==========================================\n")
    if not args.no_browser:
        open_when_ready(args.port, url)
    try:
        server.run()
    finally:
        st = read_state()
        if st and st.get("pid") == os.getpid():
            state_path().unlink(missing_ok=True)
        print("演示服务已停止。")


if __name__ == "__main__":
    main()
