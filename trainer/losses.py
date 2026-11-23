"""VITS/RVC losses and spectrogram helpers (port of RVC-WebUI infer/lib/train, MIT)."""
from __future__ import annotations

import torch
from librosa.filters import mel as librosa_mel

from trainer.common import HOP, N_FFT, N_MELS, SR, WIN

_windows: dict = {}
_mel_basis: dict = {}


def spectrogram(y: torch.Tensor) -> torch.Tensor:
    """[B, T] waveform -> [B, n_fft/2+1, frames] linear magnitude (float32)."""
    key = (str(y.device),)
    if key not in _windows:
        _windows[key] = torch.hann_window(WIN, device=y.device)
    pad = (N_FFT - HOP) // 2
    y = torch.nn.functional.pad(y.float().unsqueeze(1), (pad, pad), mode="reflect").squeeze(1)
    spec = torch.stft(y, N_FFT, hop_length=HOP, win_length=WIN, window=_windows[key],
                      center=False, pad_mode="reflect", normalized=False, onesided=True, return_complex=True)
    return torch.sqrt(spec.real.pow(2) + spec.imag.pow(2) + 1e-6)


def spec_to_mel(spec: torch.Tensor) -> torch.Tensor:
    key = str(spec.device)
    if key not in _mel_basis:
        _mel_basis[key] = torch.from_numpy(librosa_mel(sr=SR, n_fft=N_FFT, n_mels=N_MELS, fmin=0.0, fmax=None)).float().to(spec.device)
    mel = torch.matmul(_mel_basis[key], spec.float())
    return torch.log(torch.clamp(mel, min=1e-5))


def mel_spectrogram(y: torch.Tensor) -> torch.Tensor:
    return spec_to_mel(spectrogram(y))


def feature_loss(fmap_r, fmap_g):
    loss = 0
    for dr, dg in zip(fmap_r, fmap_g):
        for rl, gl in zip(dr, dg):
            loss += torch.mean(torch.abs(rl.float().detach() - gl.float()))
    return loss * 2


def discriminator_loss(disc_real_outputs, disc_generated_outputs):
    loss = 0
    for dr, dg in zip(disc_real_outputs, disc_generated_outputs):
        loss += torch.mean((1 - dr.float()) ** 2) + torch.mean(dg.float() ** 2)
    return loss


def generator_loss(disc_outputs):
    loss = 0
    for dg in disc_outputs:
        loss += torch.mean((1 - dg.float()) ** 2)
    return loss


def kl_loss(z_p, logs_q, m_p, logs_p, z_mask):
    z_p, logs_q, m_p, logs_p, z_mask = (t.float() for t in (z_p, logs_q, m_p, logs_p, z_mask))
    kl = logs_p - logs_q - 0.5
    kl += 0.5 * ((z_p - m_p) ** 2) * torch.exp(-2.0 * logs_p)
    return torch.sum(kl * z_mask) / torch.sum(z_mask)
