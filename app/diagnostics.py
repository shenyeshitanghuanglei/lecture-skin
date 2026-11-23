"""Diagnostics bundle: one zip a user can attach to a bug report.

Collects the app version, system / GPU / memory facts, the tail of every log and the
saved settings, with the user's name and home folder masked. Standard library only, so
both the desktop window (even when the server failed to start) and the server can use it.
"""
from __future__ import annotations

import ctypes
import getpass
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "vendor" / "voice-changer" / "server"
OUT_DIR = ROOT / "logs" / "diagnostics"
LOG_TAIL_BYTES = 1024 * 1024  # last 1 MB of each log is plenty and keeps the zip small
KEEP_BUNDLES = 5
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# Small settings files worth including as-is (they hold no secrets, only device names / numbers).
SETTING_FILES = [
    SERVER_DIR / "stored_setting.json",
    SERVER_DIR / "vt_devices.json",
    ROOT / "logs" / "default-output.json",
]


def _run(cmd: list[str], timeout: float = 8) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT,
                           creationflags=_NO_WINDOW, encoding="utf-8", errors="replace")
        return (r.stdout or r.stderr).strip()
    except Exception as e:
        return f"<{type(e).__name__}: {e}>"


def _version() -> dict[str, str]:
    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    info = {"app": m.group(1) if m else "?"}
    if shutil.which("git") and (ROOT / ".git").exists():
        info["git"] = _run(["git", "describe", "--tags", "--always", "--dirty"])
    return info


def _memory() -> dict[str, float]:
    if sys.platform != "win32":
        return {}

    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
            (n, ctypes.c_ulonglong) for n in ("totalPhys", "availPhys", "totalPage", "availPage",
                                              "totalVirt", "availVirt", "availExt")]

    st = MS()
    st.dwLength = ctypes.sizeof(st)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return {}
    gb = 1024**3
    return {"ramTotalGB": round(st.totalPhys / gb, 1), "ramFreeGB": round(st.availPhys / gb, 1),
            "commitTotalGB": round(st.totalPage / gb, 1), "commitFreeGB": round(st.availPage / gb, 1)}


def _gpu() -> str:
    if shutil.which("nvidia-smi"):
        return _run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used",
                     "--format=csv,noheader"])
    if sys.platform == "win32":
        return _run(["powershell", "-NoProfile", "-Command",
                     "Get-CimInstance Win32_VideoController | ForEach-Object { \"$($_.Name), $($_.DriverVersion)\" }"],
                    timeout=15)
    return "unknown"


def system_info() -> dict[str, Any]:
    info: dict[str, Any] = {"time": time.strftime("%Y-%m-%d %H:%M:%S %z")}
    for key, fn in (("version", _version), ("memory", _memory), ("gpu", _gpu)):
        try:
            info[key] = fn()
        except Exception as e:
            info[key] = f"<{type(e).__name__}: {e}>"
    info["os"] = platform.platform()
    info["python"] = sys.version.split()[0]
    venv_py = ROOT / ".venv" / "Scripts" / "python.exe"
    info["venvPython"] = _run([str(venv_py), "--version"]) if venv_py.exists() else "missing"
    return info


def _masks() -> list[tuple[str, str]]:
    """Strings that would identify the user, longest first, and what to replace them with."""
    pairs = []
    home = str(Path.home())
    for h in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:
        pairs.append((h, "%USERPROFILE%"))
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    if len(user) >= 3:  # very short names would mangle unrelated text
        pairs.append((user, "<user>"))
    return sorted(pairs, key=lambda p: -len(p[0]))


def redact(text: str, masks: list[tuple[str, str]] | None = None) -> str:
    for needle, repl in masks if masks is not None else _masks():
        text = re.sub(re.escape(needle), repl, text, flags=re.I)
    return text


def _tail(path: Path, limit: int | None = None) -> str:
    limit = limit or LOG_TAIL_BYTES
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        f.seek(max(0, size - limit))
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    if size > limit:
        text = f"... (前 {size - limit} 字节已省略)\n" + text.split("\n", 1)[-1]
    return text


def build_bundle(extra: dict[str, Any] | None = None, out_dir: Path = OUT_DIR,
                 log_dir: Path | None = None, setting_files: list[Path] | None = None) -> Path:
    """Write the zip and return its path. `extra` holds live state from the server
    (status, devices, thread stacks) when it is reachable."""
    log_dir = log_dir or ROOT / "logs"
    setting_files = SETTING_FILES if setting_files is None else setting_files
    out_dir.mkdir(parents=True, exist_ok=True)
    masks = _masks()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"voice-panel-diagnostics-{stamp}.zip"
    problems: list[str] = []

    def dump(obj: Any) -> str:
        return redact(json.dumps(obj, ensure_ascii=False, indent=2, default=str), masks)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("system.json", dump(system_info()))
        for name, value in (extra or {}).items():
            z.writestr(f"live/{name}.json", dump(value))
        for log in sorted(log_dir.glob("*.log")):
            try:
                z.writestr(f"logs/{log.name}", redact(_tail(log), masks))
            except OSError as e:
                problems.append(f"{log.name}: {e}")
        for f in setting_files:
            if f.exists():
                try:
                    z.writestr(f"settings/{f.name}", redact(f.read_text(encoding="utf-8", errors="replace"), masks))
                except OSError as e:
                    problems.append(f"{f.name}: {e}")
        if problems:
            z.writestr("problems.txt", "\n".join(problems))
        z.writestr("README.txt",
                   "声音面板诊断包：版本、系统/显卡/内存信息、日志末尾和设置。\n"
                   "用户名和用户目录已替换为 <user> / %USERPROFILE%；不包含任何录音或声音模型。\n")
    _prune(out_dir)
    return path


def _prune(out_dir: Path) -> None:
    bundles = sorted(out_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in bundles[KEEP_BUNDLES:]:
        try:
            old.unlink()
        except OSError:
            pass


def reveal(path: Path) -> None:
    """Show the file selected in Explorer so the user can drag it into a bug report."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)], creationflags=_NO_WINDOW)
