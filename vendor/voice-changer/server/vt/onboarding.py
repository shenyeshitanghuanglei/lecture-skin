"""Onboarding environment checks, auto-installer for uv/Python, and safe external links."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]

ALLOWED_URLS: dict[str, str] = {
    "vb_cable": "https://vb-audio.com/Cable/",
    "report_issue": "https://github.com/shenyeshitanghuanglei/lecture-skin/issues/new?template=bug_report.yml",
}

UV_INSTALL_URL = "https://astral.sh/uv/install.ps1"
REQUIRED_PYTHON = (3, 12)


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def find_uv() -> str | None:
    """Locate uv executable on PATH or common user install locations."""
    found = shutil.which("uv")
    if found:
        return found
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / ("uv.exe" if sys.platform == "win32" else "uv"),
        home / ".cargo" / "bin" / ("uv.exe" if sys.platform == "win32" else "uv"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def _run_version(cmd: list[str], timeout: float = 5.0) -> str | None:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_creation_flags(),
        )
        if proc.returncode == 0:
            out = (proc.stdout or proc.stderr or "").strip()
            return out.splitlines()[0] if out else None
    except Exception:
        pass
    return None


def check_python() -> dict[str, Any]:
    """Check the running Python version."""
    ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    ok = (sys.version_info.major, sys.version_info.minor) == REQUIRED_PYTHON
    return {
        "installed": True,
        "ok": ok,
        "version": ver,
        "required": f"{REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}",
    }


def check_uv() -> dict[str, Any]:
    """Check whether uv is installed and query its version."""
    uv_path = find_uv()
    version = _run_version([uv_path, "--version"]) if uv_path else None
    return {
        "installed": uv_path is not None,
        "version": version,
    }


def check_vb_cable(inputs: list[Any]) -> dict[str, Any]:
    """Detect VB-CABLE virtual audio device from PortAudio input list."""
    cable = next((d for d in inputs if "CABLE" in getattr(d, "name", "").upper()), None)
    return {
        "installed": cable is not None,
        "deviceName": getattr(cable, "name", None) if cable is not None else None,
        "downloadUrl": ALLOWED_URLS["vb_cable"],
    }


def open_allowed_url(target: str) -> str:
    """Open a whitelisted URL in the user's default system browser."""
    url = ALLOWED_URLS.get(target)
    if url is None:
        raise ValueError(f"不支持的链接目标: {target}")
    if sys.platform == "win32":
        os.startfile(url)  # type: ignore[attr-defined]
    else:
        webbrowser.open(url)
    return url


@dataclass
class _TaskState:
    status: str = "idle"  # idle | running | success | error
    step: str = ""
    message: str = ""
    logs: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "step": self.step,
            "message": self.message,
            "logs": list(self.logs[-30:]),
        }


class EnvInstaller:
    """Thread-safe background installer for missing uv / Python 3.12."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = _TaskState()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._state.snapshot()

    def _set(self, *, status: str | None = None, step: str | None = None, message: str | None = None) -> None:
        with self._lock:
            if status is not None:
                self._state.status = status
            if step is not None:
                self._state.step = step
            if message is not None:
                self._state.message = message

    def _log(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        with self._lock:
            self._state.logs.append(text)
            if len(self._state.logs) > 60:
                self._state.logs = self._state.logs[-60:]

    def _run_cmd(self, cmd: list[str], timeout: float = 300.0) -> int:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            creationflags=_creation_flags(),
            timeout=timeout,
        )
        return proc.returncode

    def ensure_async(self) -> dict[str, Any]:
        """Start background check & auto-install if not already running."""
        with self._lock:
            if self._state.status == "running":
                return self._state.snapshot()
            self._state = _TaskState(status="running", step="checking", message="正在检查 Python 和 uv…")
        threading.Thread(target=self._worker, name="vt-env-installer", daemon=True).start()
        return self.status()

    def _worker(self) -> None:
        try:
            uv_path = find_uv()
            if not uv_path:
                self._set(step="install_uv", message="未检测到 uv，正在自动安装…")
                self._log(f"开始从官方脚本安装 uv ({UV_INSTALL_URL})")
                if sys.platform == "win32":
                    cmd = [
                        "powershell",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        f"irm {UV_INSTALL_URL} | iex",
                    ]
                else:
                    cmd = ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"]
                rc = self._run_cmd(cmd, timeout=240.0)
                if rc != 0:
                    raise RuntimeError("uv 自动安装失败，请检查网络或代理设置后重试")
                uv_path = find_uv()
                if not uv_path:
                    raise RuntimeError("uv 安装已结束但未找到可执行文件，请重启面板后重试")
                self._log("uv 安装成功")
            else:
                self._log("已检测到 uv")

            py = check_python()
            if not py["ok"]:
                self._set(step="install_python", message="当前 Python 非 3.12，正在通过 uv 自动安装 Python 3.12…")
                self._log("执行：uv python install 3.12")
                rc = self._run_cmd([uv_path, "python", "install", "3.12"], timeout=300.0)
                if rc != 0:
                    raise RuntimeError("Python 3.12 自动安装失败，请检查网络后重试")
                self._log("Python 3.12 安装完成")
            else:
                self._log(f"已检测到 Python {py['version']}")

            self._set(status="success", step="done", message="Python 与 uv 环境已就绪")
        except RuntimeError as error:
            self._set(status="error", step="error", message=str(error))
        except subprocess.TimeoutExpired:
            self._set(status="error", step="error", message="自动安装超时，请检查网络后重试")
        except Exception:
            logger.exception("[onboarding] auto-install failed")
            self._set(status="error", step="error", message="自动安装失败，请运行一键安装脚本查看详情")
