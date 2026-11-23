"""Stage 1: raw files -> cleaned 3.7 s training clips + features + a material report."""
from __future__ import annotations

import numpy as np
import soundfile as sf

from trainer import audio
from trainer.common import SR, SR16, Job, cuda_device

MIN_TRAIN_SEC = 180      # refuse to train below 3 min of usable speech
WARN_TRAIN_SEC = 300     # warn below 5 min
GOOD_TRAIN_SEC = 600     # 10 min+ is comfortable


def prepare(job: Job) -> dict:
    meta = job.meta
    files = list(meta.get("files", []))
    if not files:
        raise ValueError("还没有添加素材")
    device = cuda_device()
    job.update_meta(materialsDirty=True)

    # start from a clean slate: clips/features may come from an earlier run
    for sub in ("clips/40k", "clips/16k", "feats", "f0"):
        for p in job.path(sub).glob("*"):
            p.unlink()

    clips: list[dict] = []
    n = 0
    for fi, f in enumerate(files):
        job.check_cancel()
        name = f["name"]
        base = fi / len(files)
        job.report(state="preparing", stage="decode", pct=base * 0.6, message=f"读取 {f.get('label') or name}")
        src = job.path("raw", name)
        try:
            if f.get("separate"):
                x44 = audio.decode(src, 44100)
                job.report(message=f"分离人声 {f.get('label') or name}（{len(x44) / 44100 / 60:.1f} 分钟）")
                job.log(f"separating vocals: {name}")
                x = audio.resample(audio.separate_vocals(x44, device), 44100, SR)
            else:
                x = audio.decode(src, SR)
        except Exception as e:
            job.log(f"decode failed for {name}: {e!r}")
            f["error"] = f"无法读取：{e}"
            continue
        f["seconds"] = round(len(x) / SR, 1)
        f.pop("error", None)
        clipped = float(np.mean(np.abs(x) > 0.995)) if len(x) else 0.0
        x = audio.normalize_file(audio.highpass(x, SR))
        for s, e in audio.slice_speech(x, SR):
            seg = audio.normalize_clip(x[s:e])
            cid = f"{n:05d}"
            n += 1
            sf.write(job.path("clips", "40k", f"{cid}.wav"), seg, SR, subtype="PCM_16")
            sf.write(job.path("clips", "16k", f"{cid}.wav"), audio.resample(seg, SR, SR16), SR16, subtype="PCM_16")
            clips.append({
                "id": cid, "file": name, "start": round(s / SR, 2), "dur": round((e - s) / SR, 2),
                "clipped": clipped > 0.001, "excluded": False, "auto": None,
            })
    job.update_meta(files=_merge_files(meta["files"], files))
    if not clips:
        raise ValueError("素材里没有检测到人声（可能太安静，或者全是音乐）")

    # ---- features (also gives per-clip pitch for the report) ----
    from trainer.features import Extractor, coarse_f0

    ex = Extractor(device)
    for i, c in enumerate(clips):
        if i % 10 == 0:
            job.check_cancel()
            job.report(stage="features", pct=0.6 + 0.4 * i / len(clips), message=f"提取特征 {i}/{len(clips)}")
        x16, _ = sf.read(job.path("clips", "16k", f"{c['id']}.wav"), dtype="float32")
        units = ex.units(x16)
        f0 = ex.f0(x16)
        np.save(job.path("feats", f"{c['id']}.npy"), units.astype(np.float16))
        np.save(job.path("f0", f"{c['id']}.npy"), np.stack([coarse_f0(f0).astype(np.float32), f0]))
        voiced = f0[f0 > 0]
        c["voiced"] = round(float(len(voiced) / max(1, len(f0))), 2)
        c["f0"] = round(float(np.median(voiced)), 1) if len(voiced) else 0.0
        if c["voiced"] < 0.15:
            c["excluded"], c["auto"] = True, "几乎没有人声"
    job.write("clips.json", clips)
    rep = report(job)
    job.update_meta(materialsDirty=False)
    job.report(state="prepared", stage="done", pct=1.0, message="素材已就绪", error=None)
    job.log(f"prepared {len(clips)} clips, {rep['seconds']:.0f}s usable, median f0 {rep['f0']}")
    return rep


def _merge_files(all_files: list[dict], processed: list[dict]) -> list[dict]:
    by = {f["name"]: f for f in processed}
    return [by.get(f["name"], f) for f in all_files]


def report(job: Job) -> dict:
    """Summary of usable material; recomputed whenever clips are excluded/restored."""
    clips = job.read("clips.json", []) or []
    use = [c for c in clips if not c.get("excluded")]
    sec = sum(c["dur"] for c in use)
    f0s = [c["f0"] for c in use if c.get("f0")]
    warnings = []
    if sec < MIN_TRAIN_SEC:
        warnings.append(f"可用人声只有 {sec / 60:.1f} 分钟，至少需要 {MIN_TRAIN_SEC // 60} 分钟才能训练")
    elif sec < WARN_TRAIN_SEC:
        warnings.append("素材偏少（建议 10 分钟以上），效果可能一般")
    if sum(1 for c in use if c.get("clipped")) > len(use) * 0.3:
        warnings.append("部分素材有爆音（音量过大被削波），会影响音质")
    rep = {
        "clips": len(clips),
        "used": len(use),
        "seconds": round(sec, 1),
        "f0": round(float(np.median(f0s)), 1) if f0s else 0.0,
        "level": "bad" if sec < MIN_TRAIN_SEC else "warn" if sec < GOOD_TRAIN_SEC else "good",
        "canTrain": sec >= MIN_TRAIN_SEC,
        "warnings": warnings,
    }
    job.write("report.json", rep)
    return rep
