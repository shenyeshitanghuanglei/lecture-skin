"""Decoding, vocal separation and slicing of the source material."""
from __future__ import annotations

from pathlib import Path

import numpy as np

AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".webm"}
VIDEO_EXTS = {".mp4", ".mkv", ".flv", ".mov", ".avi", ".ts", ".m4v", ".wmv"}


def decode(path: str | Path, sr: int) -> np.ndarray:
    """Decode the first audio stream of any audio/video file to mono float32 at ``sr``."""
    import av

    out: list[np.ndarray] = []
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError("文件里没有音轨")
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="flt", layout="mono", rate=sr)
        for frame in container.decode(stream):
            for f in resampler.resample(frame):
                out.append(f.to_ndarray().reshape(-1))
        for f in resampler.resample(None):
            out.append(f.to_ndarray().reshape(-1))
    if not out:
        return np.zeros(0, np.float32)
    return np.concatenate(out).astype(np.float32)


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    import torch
    import torchaudio.functional as AF

    return AF.resample(torch.from_numpy(np.ascontiguousarray(x)), sr_in, sr_out).numpy().astype(np.float32)


_demucs = None


def separate_vocals(x44: np.ndarray, device) -> np.ndarray:
    """Return the vocal stem of a 44.1 kHz mono signal (htdemucs, MIT)."""
    global _demucs
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    if _demucs is None:
        _demucs = get_model("htdemucs").to(device).eval()
    model = _demucs
    wav = torch.from_numpy(x44).to(device)
    wav = wav.expand(model.audio_channels, -1)[None]  # [1, 2, T]
    ref = wav.mean(1)
    mean, std = ref.mean(), ref.std() + 1e-8
    with torch.no_grad():
        sources = apply_model(model, (wav - mean) / std, split=True, overlap=0.25, progress=False, device=device)
    vocals = sources[0, model.sources.index("vocals")] * std + mean
    return vocals.mean(0).float().cpu().numpy()


def highpass(x: np.ndarray, sr: int, cutoff: float = 48.0) -> np.ndarray:
    from scipy.signal import butter, sosfiltfilt

    sos = butter(5, cutoff, btype="high", fs=sr, output="sos")
    return sosfiltfilt(sos, x).astype(np.float32)


def rms_db(x: np.ndarray, sr: int, hop_s: float = 0.01, win_s: float = 0.04) -> np.ndarray:
    hop, win = int(sr * hop_s), int(sr * win_s)
    if len(x) < win:
        return np.full(1, 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9))
    n = 1 + (len(x) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    return 20 * np.log10(np.sqrt(np.mean(x[idx] ** 2, axis=1)) + 1e-9)


def slice_speech(
    x: np.ndarray,
    sr: int,
    threshold_db: float = -42.0,
    min_silence: float = 0.3,
    keep_silence: float = 0.15,
    max_len: float = 3.7,
    overlap: float = 0.3,
    min_len: float = 0.8,
) -> list[tuple[int, int]]:
    """Split at pauses, then cut long phrases into ~3.7 s pieces (RVC's preprocessing).

    Returns (start, end) sample ranges.
    """
    db = rms_db(x, sr)
    hop = int(sr * 0.01)
    silent = db < threshold_db
    # voiced runs separated by silences of at least ``min_silence``
    spans: list[list[int]] = []
    i, n, gap = 0, len(silent), int(min_silence / 0.01)
    while i < n:
        if silent[i]:
            i += 1
            continue
        j = i
        while j < n:
            if silent[j]:
                k = j
                while k < n and silent[k]:
                    k += 1
                if k - j >= gap or k == n:
                    break
                j = k
            else:
                j += 1
        spans.append([i, j])
        i = j
    keep = int(keep_silence / 0.01)
    out: list[tuple[int, int]] = []
    step, length = int((max_len - overlap) * sr), int(max_len * sr)
    for a, b in spans:
        s = max(0, (a - keep) * hop)
        e = min(len(x), (b + keep) * hop + int(sr * 0.04))
        while e - s > length + int(min_len * sr):
            out.append((s, s + length))
            s += step
        if e - s >= int(min_len * sr):
            out.append((s, e))
    return out


def normalize_file(x: np.ndarray) -> np.ndarray:
    """Bring a whole recording to a consistent level (robust peak at 0.9)."""
    if not len(x):
        return x
    peak = np.percentile(np.abs(x), 99.9)
    if peak < 1e-4:
        return x
    return np.clip(x * (0.9 / peak), -1.0, 1.0).astype(np.float32)


def normalize_clip(x: np.ndarray) -> np.ndarray:
    """RVC's per-slice normalisation: blend of peak-normalised (0.9 * 0.75) and original."""
    peak = np.abs(x).max()
    if peak < 1e-4:
        return x
    return (x / peak * (0.9 * 0.75) + 0.25 * x).astype(np.float32)
