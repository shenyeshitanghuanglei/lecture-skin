"""声音面板 — desktop window for the voice changer (voice-translater).

Starts the voice changer server in the background if it isn't running, shows the
control panel in a native window (pywebview + WebView2), and makes CABLE Input the
Windows default output while open. On close it stops every voice server process and
puts the real speakers/headphones back as the default output, so sound plays normally.

Run:  .venv\\Scripts\\pythonw.exe app\\desktop.py
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "vendor" / "voice-changer" / "server"
PORT = 18888
BASE = f"http://127.0.0.1:{PORT}"
PANEL_URL = f"{BASE}/panel/"
LOG_DIR = ROOT / "logs"
STARTUP_TIMEOUT_S = 240  # first run may download weights
WINDOW_TITLE = "声音面板"

# Local-only traffic: keep it away from any system proxy (e.g. 127.0.0.1:7897).
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))
os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--proxy-bypass-list=<-loopback>")

SPLASH = """<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;height:100vh;display:grid;place-items:center;font:15px "Microsoft YaHei UI",sans-serif;
background:#f4f5f8;color:#1c1f26}@media(prefers-color-scheme:dark){body{background:#111318;color:#e7e9ee}}
.b{text-align:center}.d{width:38px;height:38px;border:4px solid #8b7dff55;border-top-color:#6d5dfc;border-radius:50%;
margin:0 auto 18px;animation:s 1s linear infinite}@keyframes s{to{transform:rotate(360deg)}}
small{opacity:.6;display:block;max-width:520px;margin:0 auto;line-height:1.6}
.d.x{animation:none;border-color:#e0a23a;}#k{margin-top:18px}#k:empty{display:none}
button{font:inherit;margin:0 6px;padding:7px 18px;border-radius:8px;border:1px solid #6d5dfc;cursor:pointer;
background:#6d5dfc;color:#fff}button.g{background:transparent;color:inherit;border-color:#8888}
</style></head><body><div class="b"><div class="d" id="d"></div>
<div id="t">正在启动变声服务…</div><small id="s">通常需要 20–40 秒，开机后首次打开会更久</small><div id="k"></div></div>
<script>
function vtStatus(title, detail, warn, buttons){
  document.getElementById('t').textContent = title;
  document.getElementById('s').textContent = detail || '';
  document.getElementById('d').className = warn ? 'd x' : 'd';
  var k = document.getElementById('k'); k.innerHTML = '';
  (buttons || []).forEach(function(b, i){
    var e = document.createElement('button'); e.textContent = b[0]; if (i) e.className = 'g';
    e.onclick = function(){ window.pywebview.api[b[1]](); }; k.appendChild(e);
  });
}
</script></body></html>"""

# The server (torch + CUDA + models) commits roughly 6 GB. Starting it with less free
# commit (RAM + page file) makes it crawl and then fail to load the model (MemoryError).
SERVER_COMMIT_GB = 6.0
MODEL_FAIL_MARK = "Failed to create pipeline"


def _acquire_single_instance() -> object | None:
    """Prevent duplicate panel windows fighting over WebView2 storage and server lifecycle."""
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    mutex = kernel32.CreateMutexW(None, False, "Local\\VoiceTranslaterDesktopPanel")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
        if mutex:
            kernel32.CloseHandle(mutex)
        return None
    return mutex


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def free_commit_gb() -> float | None:
    """Commit memory still available (physical RAM + page file), in GB; None if unknown."""
    if sys.platform != "win32":
        return None
    st = _MemoryStatusEx()
    st.dwLength = ctypes.sizeof(st)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return None
    return st.ullAvailPageFile / 1024**3


def model_load_failure(log_text: str) -> str | None:
    """Why the voice model failed to load, judged from this run's server log; None if it loaded."""
    if MODEL_FAIL_MARK not in log_text:
        return None
    tail = log_text[log_text.index(MODEL_FAIL_MARK):]
    if "MemoryError" in tail or "out of memory" in tail.lower():
        return "memory"
    return "other"


def server_alive() -> bool:
    try:
        with _LOCAL.open(f"{BASE}/api/hello", timeout=2):
            return True
    except Exception:
        return False


def post(path: str, timeout: float = 2.5) -> None:
    try:
        _LOCAL.open(urllib.request.Request(f"{BASE}{path}", data=b"", method="POST"), timeout=timeout).read()
    except Exception:
        pass


class Server:
    """Owns the server process only if we started it."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self._closing = False
        self._lock = threading.Lock()
        self._log_offset: int | None = None  # where this run's output starts in the log

    LOG = LOG_DIR / "server-console.log"

    def ensure_running(self) -> None:
        with self._lock:
            if self._closing or server_alive():
                return
            LOG_DIR.mkdir(exist_ok=True)
            python = ROOT / ".venv" / "Scripts" / "python.exe"
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self._log_offset = self.LOG.stat().st_size if self.LOG.exists() else 0
            with open(self.LOG, "ab") as log:
                self.proc = subprocess.Popen(
                    [str(python), "main.py"], cwd=SERVER_DIR, stdout=log, stderr=subprocess.STDOUT, creationflags=flags
                )

    def wait_ready(self) -> bool:
        deadline = time.time() + STARTUP_TIMEOUT_S
        while time.time() < deadline and not self._closing:
            if server_alive():
                return True
            if self.proc is not None and self.proc.poll() is not None:
                return False  # crashed during startup
            time.sleep(0.4)
        return False

    def watch(self) -> None:
        """Auto-restart the server if we own it and it exits unexpectedly mid-session."""
        while not self._closing:
            time.sleep(3.0)
            if self._closing:
                break
            if self.proc is not None and self.proc.poll() is not None and not server_alive():
                self.ensure_running()

    def run_log(self) -> str:
        """Server output of the run we started ('' if we didn't start it)."""
        if self._log_offset is None:
            return ""
        try:
            with open(self.LOG, "rb") as f:
                f.seek(self._log_offset)
                return f.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def shutdown(self) -> None:
        """Closing the panel ends *every* voice server, including one left over from an
        earlier session or started by start.ps1, so nothing keeps running in the background."""
        self._closing = True
        self.stop()

    def stop(self) -> None:
        post("/vt/api/stop", timeout=2.5)
        with self._lock:
            pids = set(_listening_pids(PORT))
            if self.proc is not None and self.proc.poll() is None:
                pids.add(self.proc.pid)
            for pid in pids:
                _kill_tree(pid)
            if self.proc is not None:
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            deadline = time.time() + 5
            while server_alive() and time.time() < deadline:
                time.sleep(0.2)


def _kill_tree(pid: int) -> None:
    # The venv's python.exe is a launcher that spawns the real interpreter as a
    # child, so kill the whole tree or the server would be left orphaned.
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def _listening_pids(port: int) -> list[int]:
    """PIDs listening on our port (the server, whoever started it)."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                             creationflags=subprocess.CREATE_NO_WINDOW, timeout=10).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3].upper() == "LISTENING":
            try:
                pid = int(parts[4])
            except ValueError:
                continue
            if pid > 0 and pid != os.getpid():
                pids.append(pid)
    return pids


class SystemOutput:
    """While the panel is open Windows' default output is CABLE Input (every app's sound goes
    through the voice server); on close the real speakers/headphones are made default again,
    so the computer plays sound normally once the server is gone.

    The device to restore is saved to disk, so it is still known after a crash."""

    STATE = LOG_DIR / "default-output.json"

    def _saved(self) -> dict | None:
        try:
            return json.loads(self.STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _save(self, ep) -> None:
        LOG_DIR.mkdir(exist_ok=True)
        self.STATE.write_text(json.dumps({"id": ep.id, "name": ep.name}, ensure_ascii=False), encoding="utf-8")

    def route_through_cable(self) -> None:
        try:
            import winaudio
            current = winaudio.default_output()
            if current is not None and current.audible:
                self._save(current)  # what to put back on close
            cable = next((e for e in winaudio.list_outputs() if e.is_cable), None)
            if cable is not None and (current is None or current.id != cable.id):
                winaudio.set_default_output(cable.id)
        except Exception as e:
            _log(f"could not route system audio through CABLE: {e}")

    def restore(self) -> None:
        try:
            import winaudio
            current = winaudio.default_output()
            if current is not None and current.audible:
                return  # already a real speaker/headphone
            outputs = winaudio.list_outputs()
            saved = self._saved() or {}
            target = next((e for e in outputs if e.id == saved.get("id")), None)
            if target is None or not target.audible:
                target = next((e for e in outputs if e.audible), None)
            if target is not None:
                winaudio.set_default_output(target.id)
        except Exception as e:
            _log(f"could not restore default output: {e}")


class BootControls:
    """Buttons on the splash screen (exposed to JS as window.pywebview.api)."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._choice: str | None = None

    def _pick(self, choice: str) -> None:
        self._choice = choice
        self._event.set()

    def start_anyway(self) -> None:
        self._pick("start_anyway")

    def retry(self) -> None:
        self._pick("retry")

    def open_anyway(self) -> None:
        self._pick("open_anyway")

    def reset(self) -> None:
        self._choice = None
        self._event.clear()

    def wait(self, timeout: float) -> str | None:
        """The button pressed within `timeout` seconds, else None."""
        if self._event.wait(timeout):
            choice, self._choice = self._choice, None
            self._event.clear()
            return choice
        return None


def _log(msg: str) -> None:
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with open(LOG_DIR / "desktop.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def main() -> None:
    mutex = _acquire_single_instance()
    if mutex is None:
        return

    import webview  # pywebview

    server = Server()
    system_output = SystemOutput()
    controls = BootControls()
    window = webview.create_window(
        WINDOW_TITLE, html=SPLASH, width=1120, height=780, min_size=(760, 560), background_color="#f4f5f8",
        js_api=controls,
    )

    def status(title: str, detail: str = "", warn: bool = False, buttons: list | None = None) -> None:
        args = ", ".join(json.dumps(a, ensure_ascii=False) for a in (title, detail, warn, buttons or []))
        try:
            window.evaluate_js(f"vtStatus({args})")
        except Exception:
            pass

    def wait_for_memory() -> None:
        """Hold off starting the server while free commit memory is too low; continue by
        itself once programs are closed, or right away if the user insists."""
        controls.reset()
        while not server._closing:
            free = free_commit_gb()
            if free is None or free >= SERVER_COMMIT_GB:
                return
            status(
                "可用内存不足，暂缓启动",
                f"当前剩余约 {free:.1f} GB，变声服务需要约 {SERVER_COMMIT_GB:.0f} GB。"
                "请关闭一些程序（浏览器标签、ChatGPT、VS Code 等），内存够了会自动继续。",
                warn=True, buttons=[["仍然启动", "start_anyway"]],
            )
            if controls.wait(2.0) == "start_anyway":
                return

    def boot() -> None:
        while not server._closing:
            if not server_alive():
                wait_for_memory()
                status("正在启动变声服务…", "通常需要 20–40 秒，开机后首次打开会更久")
            server.ensure_running()
            if not server.wait_ready():
                if server._closing:
                    return
                status("变声服务启动失败", f"详情见 {server.LOG}", warn=True, buttons=[["重试", "retry"]])
            else:
                reason = model_load_failure(server.run_log())
                if reason is None:
                    system_output.route_through_cable()
                    window.load_url(PANEL_URL)
                    threading.Thread(target=server.watch, name="vt-server-watch", daemon=True).start()
                    return
                _log(f"voice model failed to load ({reason})")
                if reason == "memory":
                    detail = ("内存不够，声音模型没有加载上，现在打开面板也无法变声。请关闭一些程序后点「重试」。"
                              "如果经常这样，建议把 Windows 虚拟内存（页面文件）调大到 16 GB 或「系统管理的大小」。")
                else:
                    detail = f"声音模型没有加载上，现在打开面板也无法变声。详情见 {server.LOG}"
                status("声音模型加载失败", detail, warn=True,
                       buttons=[["重试", "retry"], ["仍然打开面板", "open_anyway"]])
            controls.reset()
            choice = None
            while choice is None and not server._closing:
                choice = controls.wait(1.0)
            if choice == "open_anyway":
                system_output.route_through_cable()
                window.load_url(PANEL_URL)
                threading.Thread(target=server.watch, name="vt-server-watch", daemon=True).start()
                return
            # retry: drop the broken server and start over
            status("正在重新启动变声服务…", "正在关闭上一次的服务")
            server.stop()

    # Clean up on the main thread once the window is gone. Not via window.events.closed:
    # pywebview runs that handler on a daemon thread, which is killed as soon as the
    # process exits, so the server used to survive the panel.
    try:
        webview.start(boot, gui="edgechromium", private_mode=False, storage_path=str(ROOT / "logs" / "webview"))
    finally:
        server.shutdown()
        system_output.restore()


if __name__ == "__main__":
    main()
