"""Stage 2: fine-tune the RVC v2 40k base model on the prepared clips.

Streamlined single-GPU port of RVC-WebUI ``infer/modules/train/train.py`` (MIT):
same model, losses, optimiser and schedule; bf16 autocast instead of fp16+GradScaler.
"""
from __future__ import annotations

import math
import random
import time
from collections import OrderedDict

import numpy as np
import soundfile as sf
import torch
import torch.nn.functional as F

from trainer import losses
from trainer.common import HOP, MODEL_CONFIG, SEGMENT, SR, SR16, Job, cuda_device, ensure_pretrained

PRESETS = {
    # epochs for ~10 min of material; scaled by material length (fewer clips -> more epochs)
    "fast": {"label": "快速", "epochs": 100},
    "standard": {"label": "标准", "epochs": 200},
    "fine": {"label": "精细", "epochs": 400},
}
N_CHECKPOINTS = 8
KEEP_CKPTS = 8


def plan_epochs(preset: str, seconds: float) -> int:
    base = PRESETS.get(preset, PRESETS["standard"])["epochs"]
    # keep the number of optimiser steps roughly comparable: short sets need more passes
    scale = min(2.0, max(0.5, math.sqrt(600 / max(seconds, 60))))
    return int(round(base * scale / 10) * 10)


# Peak reserved memory per batch size, measured on an RTX 5070 Laptop (bf16, 3.7 s clips).
BATCH_GB = {8: 4.9, 6: 3.9, 4: 2.9, 2: 2.0}
FRAME_BUCKET = 32  # pad batches to a multiple of this many frames: few distinct shapes


def memory_budget(device) -> float:
    """GB this process may use: what is free now minus headroom for Windows and other apps.

    On Windows a CUDA process that outgrows the card does not fail: the driver quietly
    pages to system RAM and training gets ~50x slower (seen as one epoch taking minutes).
    Capping PyTorch's allocator below the free amount makes it recycle its cache instead.
    """
    if device.type != "cuda":
        return 0.0
    free, total = torch.cuda.mem_get_info(device)
    # headroom: CUDA context/cuDNN (~0.5 GB, outside the allocator cap) + other apps growing
    budget = free / 2**30 - 1.5
    frac = max(0.2, min(0.95, budget * 2**30 / total))
    torch.cuda.set_per_process_memory_fraction(frac, device)
    return budget


def pick_batch_size(budget_gb: float) -> int:
    for bs in (8, 6, 4):
        if budget_gb >= BATCH_GB[bs] + 0.2:
            return bs
    return 2


# ------------------------------------------------------------------ data
class Clips:
    """All usable clips held in memory (30 min of speech is ~150 MB)."""

    def __init__(self, job: Job):
        self.items = []
        for c in job.read("clips.json", []):
            if c.get("excluded"):
                continue
            wav, sr = sf.read(job.path("clips", "40k", f"{c['id']}.wav"), dtype="float32")
            units = np.load(job.path("feats", f"{c['id']}.npy")).astype(np.float32)
            f0 = np.load(job.path("f0", f"{c['id']}.npy"))
            phone = np.repeat(units, 2, axis=0)  # 50 -> 100 frames/s
            n = min(len(phone), f0.shape[1], len(wav) // HOP)
            if n < SEGMENT // HOP + 2:
                continue
            self.items.append((
                torch.from_numpy(phone[:n]),
                torch.from_numpy(f0[0, :n].astype(np.int64)),
                torch.from_numpy(f0[1, :n].astype(np.float32)),
                torch.from_numpy(wav[: n * HOP]),
            ))
        if not self.items:
            raise ValueError("没有可用的训练片段")
        self.seconds = sum(len(x[3]) for x in self.items) / SR

    def batches(self, bs: int):
        """Length-bucketed random batches (clips are mostly ~3.7 s so padding is small)."""
        idx = sorted(range(len(self.items)), key=lambda i: len(self.items[i][3]) + random.random() * SR * 0.3)
        groups = [idx[i:i + bs] for i in range(0, len(idx), bs)]
        random.shuffle(groups)
        for g in groups:
            yield collate([self.items[i] for i in g])


def collate(items):
    n = -(-max(len(p[0]) for p in items) // FRAME_BUCKET) * FRAME_BUCKET
    b = len(items)
    phone = torch.zeros(b, n, 768)
    pitch = torch.zeros(b, n, dtype=torch.long)
    pitchf = torch.zeros(b, n)
    wav = torch.zeros(b, n * HOP)
    lengths = torch.zeros(b, dtype=torch.long)
    for i, (p, c, f, w) in enumerate(items):
        k = len(p)
        phone[i, :k], pitch[i, :k], pitchf[i, :k], wav[i, : len(w)] = p, c, f, w
        lengths[i] = k
    return phone, pitch, pitchf, wav, lengths


# ------------------------------------------------------------------ models
def _allow_autograd_tanh(models_module) -> None:
    """The shared model code ends with ``torch.tanh(x, out=x)`` (an inference-only memory
    saving that autograd rejects). Give that module a ``torch`` whose tanh drops ``out=``;
    the real torch module (and TorchScript) are untouched."""
    import types

    if isinstance(models_module.torch, types.ModuleType) and getattr(models_module.torch, "_vt_proxy", False):
        return

    class _Torch(types.ModuleType):
        _vt_proxy = True

        def __getattr__(self, name):
            return getattr(torch, name)

        @staticmethod
        def tanh(x, out=None):
            return torch.tanh(x)

    models_module.torch = _Torch("torch")


def build(device):
    from voice_changer.RVC.inferencer.rvc_models.infer_pack import models as rvc_models
    from voice_changer.RVC.inferencer.rvc_models.infer_pack.models import (
        MultiPeriodDiscriminatorV2,
        SynthesizerTrnMs768NSFsid,
    )

    _allow_autograd_tanh(rvc_models)

    net_g = SynthesizerTrnMs768NSFsid(*MODEL_CONFIG, is_half=False).to(device)
    net_d = MultiPeriodDiscriminatorV2(use_spectral_norm=False).to(device)
    return net_g, net_d


def load_state(path, weights_only=True):
    return torch.load(path, map_location="cpu", weights_only=weights_only)


def export_weights(net_g) -> OrderedDict:
    """Small inference checkpoint (same layout RVC-WebUI's ``savee`` writes)."""
    opt = OrderedDict()
    opt["weight"] = OrderedDict((k, v.detach().half().cpu()) for k, v in net_g.state_dict().items() if "enc_q" not in k)
    opt["config"] = list(MODEL_CONFIG)
    opt["info"] = "voice-translater clone"
    opt["sr"] = "40k"
    opt["f0"] = 1
    opt["version"] = "v2"
    return opt


# ------------------------------------------------------------------ preview
class Preview:
    """Converts a fixed sentence with the current generator so checkpoints can be compared by ear."""

    def __init__(self, job: Job, device, target_f0: float):
        self.ok = False
        src = job.path("preview_src.wav")
        if not src.exists():
            return
        from trainer.features import Extractor, coarse_f0

        x16, _ = sf.read(src, dtype="float32")
        x16 = x16[: SR16 * 12]
        ex = Extractor(device)
        units = np.repeat(ex.units(x16), 2, axis=0)
        f0 = ex.f0(x16)
        del ex
        voiced = f0[f0 > 0]
        shift = 0.0
        if len(voiced) and target_f0 > 0:
            shift = float(np.clip(12 * np.log2(target_f0 / np.median(voiced)), -15, 15))
        f0 = f0 * 2 ** (shift / 12)
        n = min(len(units), len(f0))
        self.phone = torch.from_numpy(units[:n])[None].to(device)
        self.pitch = torch.from_numpy(coarse_f0(f0[:n]))[None].to(device)
        self.pitchf = torch.from_numpy(f0[:n].astype(np.float32))[None].to(device)
        self.lengths = torch.tensor([n], device=device)
        self.shift = shift
        self.ok = True

    @torch.no_grad()
    def render(self, net_g, path) -> None:
        if not self.ok:
            return
        net_g.eval()
        sid = torch.zeros(1, dtype=torch.long, device=self.phone.device)
        n = self.phone.shape[1]
        audio = net_g.infer(self.phone, self.lengths, self.pitch, self.pitchf, sid, 0, n)[0]
        net_g.train()
        y = audio[0, 0].float().cpu().numpy()
        y = y / max(1.0, float(np.abs(y).max()) / 0.95)
        sf.write(path, y, SR, subtype="PCM_16")


# ------------------------------------------------------------------ train
def train(job: Job, preset: str = "standard", epochs: int | None = None, resume: bool = True) -> None:
    device = cuda_device()
    torch.backends.cudnn.benchmark = True
    seed = 1234
    random.seed(seed)
    torch.manual_seed(seed)

    job.report(state="training", stage="load", pct=0.0, message="准备训练", error=None)
    g_path, d_path = ensure_pretrained(job)
    data = Clips(job)
    rep = job.read("report.json", {}) or {}
    total = epochs or plan_epochs(preset, data.seconds)
    every = max(5, int(round(total / N_CHECKPOINTS / 5)) * 5)
    budget = memory_budget(device)
    bs = pick_batch_size(budget)
    job.log(f"train: {len(data.items)} clips / {data.seconds:.0f}s, epochs={total}, every={every}, batch={bs}, device={device}, gpu budget {budget:.1f}G")

    net_g, net_d = build(device)
    optim_g = torch.optim.AdamW(net_g.parameters(), 1e-4, betas=(0.8, 0.99), eps=1e-9)
    optim_d = torch.optim.AdamW(net_d.parameters(), 1e-4, betas=(0.8, 0.99), eps=1e-9)

    start_epoch = 1
    prog = job.progress()
    history = prog.get("losses", []) if resume else []
    checkpoints = prog.get("checkpoints", []) if resume else []
    last_g, last_d = job.path("ckpt", "G_last.pth"), job.path("ckpt", "D_last.pth")
    if resume and last_g.exists() and last_d.exists():
        sg, sd = load_state(last_g), load_state(last_d)
        net_g.load_state_dict(sg["model"])
        net_d.load_state_dict(sd["model"])
        optim_g.load_state_dict(sg["optimizer"])
        optim_d.load_state_dict(sd["optimizer"])
        start_epoch = int(sg["epoch"]) + 1
        # epochs after the last saved state were lost with the interruption; they will be redone
        history = [h for h in history if h["epoch"] < start_epoch]
        checkpoints = [c for c in checkpoints if c["epoch"] < start_epoch]
        job.log(f"resumed at epoch {start_epoch}")
    else:
        history, checkpoints = [], []
        for p in job.path("ckpt").glob("*.pth"):
            p.unlink()
        for p in job.path("preview").glob("e*.wav"):
            p.unlink()
        net_g.load_state_dict(load_state(g_path)["model"])
        net_d.load_state_dict(load_state(d_path)["model"])

    gamma = 0.999875
    sched_g = torch.optim.lr_scheduler.ExponentialLR(optim_g, gamma, last_epoch=-1)
    sched_d = torch.optim.lr_scheduler.ExponentialLR(optim_d, gamma, last_epoch=-1)
    for _ in range(start_epoch - 1):
        sched_g.step()
        sched_d.step()

    preview = Preview(job, device, float(rep.get("f0") or 0))
    use_bf16 = device.type == "cuda" and torch.cuda.is_bf16_supported()
    amp = dict(device_type=device.type, dtype=torch.bfloat16, enabled=use_bf16)
    seg_frames = SEGMENT // HOP

    if start_epoch == 1 and preview.ok:
        preview.render(net_g, job.path("preview", "e0.wav"))  # the untouched base model, for reference
    job.report(epochs=total, epoch=start_epoch - 1, every=every, batch=bs, preset=preset,
               losses=history, checkpoints=checkpoints, previewShift=round(getattr(preview, "shift", 0.0), 1))

    net_g.train()
    net_d.train()
    t_start, done_epochs = time.time(), 0
    for epoch in range(start_epoch, total + 1):
        t_epoch = time.time()
        acc = {"mel": 0.0, "kl": 0.0, "fm": 0.0, "gen": 0.0, "disc": 0.0}
        steps = 0
        for phone, pitch, pitchf, wav, lengths in data.batches(bs):
            job.check_cancel()
            try:
                phone, pitch, pitchf, wav, lengths = (t.to(device, non_blocking=True) for t in (phone, pitch, pitchf, wav, lengths))
                sid = torch.zeros(len(lengths), dtype=torch.long, device=device)
                with torch.no_grad():
                    spec = losses.spectrogram(wav)[:, :, : phone.shape[1]]
                with torch.autocast(**amp):
                    y_hat, ids_slice, x_mask, z_mask, (z, z_p, m_p, logs_p, m_q, logs_q) = net_g(
                        phone, lengths, pitch, pitchf, spec, lengths, sid)
                with torch.no_grad():
                    mel = losses.spec_to_mel(spec)
                    from voice_changer.RVC.inferencer.rvc_models.infer_pack import commons

                    y_mel = commons.slice_segments(mel, ids_slice, seg_frames)
                    y = commons.slice_segments(wav.unsqueeze(1), ids_slice * HOP, SEGMENT)
                y_hat_mel = losses.mel_spectrogram(y_hat.float().squeeze(1))

                # discriminator
                with torch.autocast(**amp):
                    d_r, d_g, _, _ = net_d(y, y_hat.detach())
                loss_disc = losses.discriminator_loss(d_r, d_g)
                optim_d.zero_grad(set_to_none=True)
                loss_disc.backward()
                optim_d.step()

                # generator
                with torch.autocast(**amp):
                    d_r, d_g, fmap_r, fmap_g = net_d(y, y_hat)
                loss_mel = F.l1_loss(y_mel, y_hat_mel) * 45
                loss_kl = losses.kl_loss(z_p, logs_q, m_p, logs_p, z_mask) * 1.0
                loss_fm = losses.feature_loss(fmap_r, fmap_g)
                loss_gen = losses.generator_loss(d_g)
                loss_g = loss_gen + loss_fm + loss_mel + loss_kl
                optim_g.zero_grad(set_to_none=True)
                loss_g.backward()
                optim_g.step()

            except torch.OutOfMemoryError:
                # another program took GPU memory mid-run: drop this batch, go smaller
                optim_g.zero_grad(set_to_none=True)
                optim_d.zero_grad(set_to_none=True)
                y_hat = ids_slice = z_p = m_p = logs_p = logs_q = z_mask = spec = mel = y = y_mel = None  # noqa: F841
                y_hat_mel = d_r = d_g = fmap_r = fmap_g = loss_g = loss_disc = None  # noqa: F841
                torch.cuda.empty_cache()
                if bs <= 2:
                    raise
                bs = {8: 6, 6: 4}.get(bs, 2)
                job.log(f"out of GPU memory at epoch {epoch}; batch size -> {bs}")
                job.report(batch=bs)
                break
            steps += 1
            for k, v in (("mel", loss_mel), ("kl", loss_kl), ("fm", loss_fm), ("gen", loss_gen), ("disc", loss_disc)):
                acc[k] += float(v)
        sched_g.step()
        sched_d.step()
        done_epochs += 1

        avg = {k: round(v / max(1, steps), 3) for k, v in acc.items()}
        if steps:
            history.append({"epoch": epoch, **avg})
        per_epoch = (time.time() - t_start) / done_epochs
        eta = per_epoch * (total - epoch)
        is_ckpt = epoch % every == 0 or epoch == total
        if is_ckpt:
            job.report(message=f"保存第 {epoch} 轮并生成试听")
            torch.save(export_weights(net_g), job.path("ckpt", f"G_{epoch}.pth"))
            preview.render(net_g, job.path("preview", f"e{epoch}.wav"))
            checkpoints.append({"epoch": epoch, "mel": avg["mel"],
                                "preview": f"e{epoch}.wav" if preview.ok else None})
            _prune(job, checkpoints)
            torch.save({"model": net_g.state_dict(), "optimizer": optim_g.state_dict(), "epoch": epoch}, last_g)
            torch.save({"model": net_d.state_dict(), "optimizer": optim_d.state_dict(), "epoch": epoch}, last_d)
            if device.type == "cuda":
                torch.cuda.empty_cache()  # hand the preview's buffers back instead of keeping them cached
        job.report(state="training", stage="train", epoch=epoch, pct=epoch / total, etaSec=round(eta),
                   epochSec=round(per_epoch, 1), losses=history[-400:], checkpoints=checkpoints,
                   message=f"第 {epoch}/{total} 轮")
        this_epoch = time.time() - t_epoch
        if epoch % 10 == 0 or epoch == start_epoch or this_epoch > 2 * per_epoch:
            mem = ""
            if device.type == "cuda":
                free, total_mem = torch.cuda.mem_get_info(device)
                mem = (f" mem alloc {torch.cuda.memory_allocated(device) / 2**30:.2f}G"
                       f" reserved {torch.cuda.memory_reserved(device) / 2**30:.2f}G"
                       f" peak {torch.cuda.max_memory_reserved(device) / 2**30:.2f}G"
                       f" free {free / 2**30:.2f}G")
            job.log(f"epoch {epoch}/{total} {avg} {this_epoch:.1f}s (avg {per_epoch:.1f}s){mem}")

    job.report(state="trained", stage="done", pct=1.0, etaSec=0, message="训练完成，请试听并选择一个版本")
    job.log("training finished")


def _prune(job: Job, checkpoints: list[dict]) -> None:
    """Keep the newest KEEP_CKPTS generator checkpoints (each ~55 MB)."""
    while len(checkpoints) > KEEP_CKPTS:
        # drop the oldest except the very first one, which is a useful "light training" reference
        old = checkpoints.pop(1)
        job.path("ckpt", f"G_{old['epoch']}.pth").unlink(missing_ok=True)
        job.path("preview", f"e{old['epoch']}.wav").unlink(missing_ok=True)
