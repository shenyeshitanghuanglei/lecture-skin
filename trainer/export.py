"""Stage 3: faiss index over the training units + final model file for the voice library."""
from __future__ import annotations

import re
import shutil

import numpy as np

from trainer.common import Job


def safe_name(name: str) -> str:
    """ASCII-only file stem: faiss (and some Windows APIs) mangle non-ASCII paths."""
    s = re.sub(r"[^A-Za-z0-9\-]+", "_", name).strip("_")
    return s[:30]


def build_index(job: Job) -> str:
    """Same recipe as RVC-WebUI's train_index (IVF, Flat, nprobe 1)."""
    import faiss

    clips = [c for c in job.read("clips.json", []) if not c.get("excluded")]
    feats = np.concatenate([np.load(job.path("feats", f"{c['id']}.npy")).astype(np.float32) for c in clips], 0)
    rng = np.random.default_rng(0)
    feats = feats[rng.permutation(len(feats))]
    feats = feats[:200_000]  # > ~1 h of speech: a random subset is plenty for retrieval
    n_ivf = max(1, min(int(16 * np.sqrt(len(feats))), len(feats) // 39))
    index = faiss.index_factory(768, f"IVF{n_ivf},Flat")
    faiss.extract_index_ivf(index).nprobe = 1
    index.train(feats)
    for i in range(0, len(feats), 8192):
        index.add(feats[i:i + 8192])
    name = f"added_IVF{n_ivf}_Flat_nprobe_1_{_stem(job)}_v2.index"
    out = job.path("export", name)
    for p in job.path("export").glob("*.index"):
        p.unlink()
    faiss.write_index(index, str(out))
    job.log(f"index: {len(feats)} vectors, IVF{n_ivf} -> {name}")
    return name


def export(job: Job, epoch: int) -> tuple[str, str]:
    """Copy checkpoint ``epoch`` to export/<name>.pth and (re)build the index. Returns file paths."""
    src = job.path("ckpt", f"G_{epoch}.pth")
    if not src.exists():
        raise FileNotFoundError(f"找不到第 {epoch} 轮的检查点")
    for p in job.path("export").glob("*.pth"):
        p.unlink()
    model = job.path("export", f"{_stem(job)}_e{epoch}.pth")
    shutil.copyfile(src, model)
    index = job.path("export", build_index(job))  # ~1 s; always matches the current clip selection
    return str(model), str(index)


def _stem(job: Job) -> str:
    s = safe_name(job.meta.get("name", ""))
    return f"{s}_{job.id}" if s else f"clone_{job.id}"
