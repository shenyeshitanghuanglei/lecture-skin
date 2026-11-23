"""Paths, job directory layout and progress reporting shared by all trainer stages."""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "vendor" / "voice-changer" / "server"
DATA = Path(os.environ.get("VT_DATA_DIR", ROOT / "data"))
JOBS = DATA / "clone_jobs"
PRETRAINED = DATA / "pretrained_v2"
SERVER_PRETRAIN = SERVER / "pretrain"

# The server modules (model classes, RMVPE) import each other as top-level packages.
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

SR = 40000          # model sample rate
SR16 = 16000        # ContentVec / RMVPE input rate
HOP = 400           # spectrogram hop at 40k -> 100 frames/s, same rate as RMVPE (160 @ 16k)
N_FFT = 2048
WIN = 2048
N_MELS = 125
SEGMENT = 12800     # training slice in samples (32 frames)

# config list stored in the exported checkpoint, in SynthesizerTrnMs768NSFsid argument order
MODEL_CONFIG = [
    N_FFT // 2 + 1, SEGMENT // HOP, 192, 192, 768, 2, 6, 3, 0, "1",
    [3, 7, 11], [[1, 3, 5], [1, 3, 5], [1, 3, 5]], [10, 10, 2, 2], 512, [16, 16, 4, 4],
    109, 256, SR,
]

PRETRAINED_URLS = {
    "f0G40k.pth": "https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/pretrained_v2/f0G40k.pth",
    "f0D40k.pth": "https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/pretrained_v2/f0D40k.pth",
}


class Cancelled(Exception):
    pass


class Job:
    """One cloning job: ``data/clone_jobs/<id>/``.

    raw/        uploaded / recorded source files
    clips/      40k/<n>.wav and 16k/<n>.wav training slices; clips.json lists them
    feats/      <n>.npy ContentVec layer-12 units (50 fps), f0/<n>.npy coarse + Hz pitch (100 fps)
    ckpt/       G_<epoch>.pth (generator, for previews/export), G_last.pth / D_last.pth (resume)
    preview/    e<epoch>.wav: the preview sentence converted by that checkpoint
    export/     final small .pth + .index handed to the voice library
    """

    def __init__(self, job_id: str):
        self.id = job_id
        self.dir = JOBS / job_id
        for sub in ("raw", "clips/40k", "clips/16k", "feats", "f0", "ckpt", "preview", "export"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)

    # ---- small json files -------------------------------------------------
    def path(self, *parts: str) -> Path:
        return self.dir.joinpath(*parts)

    def read(self, name: str, default: Any = None) -> Any:
        p = self.path(name)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def write(self, name: str, data: Any) -> None:
        p = self.path(name)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)

    @property
    def meta(self) -> dict:
        return self.read("job.json", {}) or {}

    def update_meta(self, **kw) -> dict:
        m = self.meta
        m.update(kw)
        self.write("job.json", m)
        return m

    # ---- progress ---------------------------------------------------------
    def progress(self) -> dict:
        return self.read("progress.json", {}) or {}

    def report(self, **kw) -> dict:
        p = self.progress()
        p.update(kw)
        p["updatedAt"] = time.time()
        self.write("progress.json", p)
        return p

    def log(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        if os.environ.get("VT_LOG_REDIRECTED") != "1":
            with open(self.path("log.txt"), "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def check_cancel(self) -> None:
        if self.path("cancel").exists():
            raise Cancelled()

    def fail(self, e: BaseException) -> None:
        if isinstance(e, Cancelled):
            self.log("cancelled")
            self.report(state="cancelled", message="已取消")
            return
        self.log("FAILED:\n" + traceback.format_exc())
        msg = str(e) or e.__class__.__name__
        if "out of memory" in msg.lower():
            msg = "显存不足：请关闭其它占用显卡的程序（游戏、视频剪辑等）后重试"
        self.report(state="failed", error=msg, message="出错了")


def ensure_pretrained(job: Job | None = None) -> tuple[Path, Path]:
    """Download the RVC v2 40k base models (MIT, ~210 MB) on first use."""
    import urllib.request

    PRETRAINED.mkdir(parents=True, exist_ok=True)
    out = []
    for name, url in PRETRAINED_URLS.items():
        p = PRETRAINED / name
        if not p.exists() or p.stat().st_size < 1_000_000:
            if job:
                job.report(message=f"正在下载底模 {name}（首次需要，约 200 MB）")
                job.log(f"downloading {url}")
            tmp = p.with_suffix(".part")
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:  # honours HTTPS_PROXY
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            os.replace(tmp, p)
        out.append(p)
    return out[0], out[1]


def cuda_device():
    import torch

    return torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
