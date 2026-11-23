"""Run a wav through the *real-time* conversion path offline and report quality metrics.

Uses the same VoiceChangerV2 + RVCr2 classes as the server, fed block by block exactly
like the audio callback does, so artefacts from block size / crossfade / gain show up
here too, without sound-card timing noise.

  python tools/offline_convert.py IN.wav OUT.wav [--slot 0] [--chunk 32] [--crossfade 0.05]
                                   [--extra 0.5] [--tran 0] [--auto-pitch 1] [--fp32] [--gain 1.0]
  python tools/offline_convert.py --metrics FILE.wav      # metrics only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "vendor" / "voice-changer" / "server"


# ---------------------------------------------------------------- metrics
def hnr_db(x: np.ndarray, sr: int) -> float:
    """Median harmonics-to-noise ratio over voiced 40 ms frames (autocorrelation method).
    Breathy / hoarse voices score lower; clean speech is typically 10-20 dB."""
    if sr > 16000:  # pitch periodicity lives well below 8 kHz; this also keeps it fast
        import librosa
        x = librosa.resample(x, orig_sr=sr, target_sr=16000)
        sr = 16000
    n = int(0.06 * sr)
    hop = n // 3
    lo, hi = int(sr / 500), int(sr / 70)
    w = np.hanning(n)
    acw = np.correlate(w, w, "full")[n - 1:]
    acw = acw / acw[0]  # Boersma (1993): divide out the window's own autocorrelation
    vals = []
    for i in range(0, len(x) - n, hop):
        f = x[i:i + n] - x[i:i + n].mean()
        f = f * w
        if np.sqrt(np.mean(f ** 2)) < 0.005:
            continue
        ac = np.correlate(f, f, "full")[n - 1:]
        if ac[0] <= 0:
            continue
        r = (ac[lo:hi] / ac[0] / acw[lo:hi]).max()
        if 0.3 < r < 0.999:  # voiced frames only
            vals.append(10 * np.log10(r / (1 - r)))
    return float(np.median(vals)) if vals else float("nan")


def mel_distance(a: np.ndarray, b: np.ndarray, sr: int) -> float:
    """Mean log-mel spectral distance (dB) after aligning b to a by cross-correlation."""
    import librosa
    from scipy.signal import correlate
    n = min(len(a), len(b), sr * 20)
    c = correlate(a[:n], b[:n], mode="full", method="fft")
    lag = int(np.argmax(c)) - (n - 1)
    if lag > 0:
        a = a[lag:]
    elif lag < 0:
        b = b[-lag:]
    n = min(len(a), len(b))
    A = librosa.power_to_db(librosa.feature.melspectrogram(y=a[:n], sr=sr, n_mels=80) + 1e-10)
    B = librosa.power_to_db(librosa.feature.melspectrogram(y=b[:n], sr=sr, n_mels=80) + 1e-10)
    loud = A.max(axis=0) > A.max() - 50  # ignore silent frames
    return float(np.sqrt(((A[:, loud] - B[:, loud]) ** 2).mean(axis=0)).mean())


def pause_level_db(x: np.ndarray, sr: int) -> float:
    """Level of the quietest 15% of 50 ms frames: how much hiss/breath fills the pauses."""
    n = int(0.05 * sr)
    r = np.array([np.sqrt(np.mean(x[i:i + n] ** 2)) for i in range(0, len(x) - n, n)])
    q = np.sort(r)[: max(1, len(r) * 15 // 100)]
    return float(20 * np.log10(q.mean() + 1e-12))


def metrics(x: np.ndarray, sr: int, block: int | None = None) -> dict:
    x = x.astype(np.float64)
    m = {
        "pause_db": round(pause_level_db(x, sr), 1),
        "peak": round(float(np.abs(x).max()), 3),
        "rms_dbfs": round(float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12)), 1),
        "clip_pct": round(float(np.mean(np.abs(x) >= 0.99) * 100), 3),
        "hnr_db": round(hnr_db(x, sr), 1),
    }
    if block:
        # Energy of the first difference right at block boundaries vs everywhere: >1 means clicks.
        d = np.abs(np.diff(x))
        idx = np.arange(block, len(d), block)
        if len(idx):
            m["boundary_jump_ratio"] = round(float(d[idx].mean() / (d.mean() + 1e-12)), 2)
    return m


# ---------------------------------------------------------------- conversion
def convert(args) -> None:
    args.input = os.path.abspath(args.input)
    args.output = os.path.abspath(args.output)
    os.chdir(SERVER)
    sys.path.insert(0, str(SERVER))
    import logging
    logging.basicConfig(level=logging.WARNING)
    from voice_changer.VoiceChangerSettings import VoiceChangerSettings
    import voice_changer.VoiceChangerV2 as vcv2

    class _NoRecorder:  # the running server holds tmp_dir/in.wav; we don't record offline
        def __init__(self, *a, **k): ...
        def write_input(self, *a): ...
        def write_output(self, *a): ...
        def close(self): ...

    vcv2.IORecorder = _NoRecorder
    VoiceChangerV2 = vcv2.VoiceChangerV2
    from voice_changer.RVC.RVCr2 import RVCr2
    from voice_changer.ModelSlotManager import ModelSlotManager
    from voice_changer.common.deviceManager.DeviceManager import DeviceManager
    from settings import get_settings

    x, sr = sf.read(args.input, dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if sr != 48000:
        import librosa
        x = librosa.resample(x, orig_sr=sr, target_sr=48000)
        sr = 48000
    x = x * args.gain

    s = VoiceChangerSettings()
    s.set_properties({
        "gpu": 0, "forceFp32": int(args.fp32), "serverReadChunkSize": args.chunk,
        "crossFadeOverlapSize": args.crossfade, "extraConvertSize": args.extra,
        "inputSampleRate": sr, "outputSampleRate": sr, "tran": args.tran,
        "autoPitch": args.auto_pitch, "autoPitchTargetHz": args.target_hz,
        "silentThreshold": args.silent, "indexRatio": args.index_ratio, "protect": args.protect,
    })
    dm = DeviceManager.get_instance()
    dm.initialize(0, int(args.fp32), 0)
    slot = ModelSlotManager.get_instance(get_settings().model_dir).get_slot_info(args.slot)
    vc = VoiceChangerV2(s)
    vc.initialize(RVCr2(slot, s))

    block = args.chunk * 128
    out = []
    for i in range(0, len(x) - block + 1, block):
        y, _vol, _perf = vc.on_request(x[i:i + block])
        y = np.asarray(y, dtype=np.float32)
        out.append(y if len(y) == block else np.zeros(block, np.float32))
    y = np.concatenate(out)
    sf.write(args.output, np.clip(y, -1, 1), sr, subtype="FLOAT")  # keep true peaks visible in metrics
    res = {"in": metrics(x, sr), "out": metrics(y, sr, block), "tran_final": s.tran,
           "settings": {k: getattr(args, k) for k in ("chunk", "crossfade", "extra", "fp32", "gain", "protect", "index_ratio")}}
    print(json.dumps(res, ensure_ascii=False))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("output", nargs="?")
    p.add_argument("--metrics", action="store_true")
    p.add_argument("--compare", help="reference wav: report mel distance of INPUT to it")
    p.add_argument("--slot", type=int, default=0)
    p.add_argument("--chunk", type=int, default=32)
    p.add_argument("--crossfade", type=float, default=0.05)
    p.add_argument("--extra", type=float, default=0.5)
    p.add_argument("--tran", type=int, default=0)
    p.add_argument("--auto-pitch", type=int, default=1)
    p.add_argument("--target-hz", type=float, default=260.0)
    p.add_argument("--index-ratio", type=float, default=0.0)
    p.add_argument("--protect", type=float, default=0.5)
    p.add_argument("--fp32", action="store_true")
    p.add_argument("--gain", type=float, default=1.0)
    p.add_argument("--silent", type=int, default=-90, help="silentThreshold dB")
    a = p.parse_args()
    if a.compare:
        x, sr = sf.read(a.input, dtype="float32", always_2d=True)
        r, sr2 = sf.read(a.compare, dtype="float32", always_2d=True)
        assert sr == sr2
        print(json.dumps({"mel_dist_db": round(mel_distance(r.mean(axis=1), x.mean(axis=1), sr), 2)}))
    elif a.metrics:
        x, sr = sf.read(a.input, dtype="float32", always_2d=True)
        print(json.dumps(metrics(x.mean(axis=1), sr), ensure_ascii=False))
    else:
        convert(a)


if __name__ == "__main__":
    main()
