"""ContentVec units + RMVPE pitch, extracted with the same weights the real-time server uses."""
from __future__ import annotations

import numpy as np

from trainer.common import SERVER_PRETRAIN

F0_MIN, F0_MAX = 50.0, 1100.0
F0_MEL_MIN = 1127 * np.log(1 + F0_MIN / 700)
F0_MEL_MAX = 1127 * np.log(1 + F0_MAX / 700)


def coarse_f0(f0: np.ndarray) -> np.ndarray:
    """Hz -> 1..255 mel bins, identical to the inference pipeline."""
    mel = 1127.0 * np.log(1.0 + f0 / 700.0)
    mel = np.where(mel > 0, (mel - F0_MEL_MIN) * 254 / (F0_MEL_MAX - F0_MEL_MIN) + 1, mel)
    return np.rint(np.clip(mel, 1, 255)).astype(np.int64)


class Extractor:
    def __init__(self, device):
        import onnxruntime as ort
        import torch
        from voice_changer.common.rmvpe.rmvpe import RMVPE

        self.device = device
        self.torch = torch
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if device.type == "cuda" else ["CPUExecutionProvider"]
        self.cv = ort.InferenceSession(str(SERVER_PRETRAIN / "content_vec_500.onnx"), providers=providers)
        self.rmvpe = RMVPE(str(SERVER_PRETRAIN / "rmvpe.pt"), is_half=False, use_jit_compile=False, device=device)

    def units(self, x16: np.ndarray) -> np.ndarray:
        """[T, 768] layer-12 units at 50 frames/s."""
        out = self.cv.run(["unit12"], {"audio": x16[None].astype(np.float32)})[0]
        return np.asarray(out, dtype=np.float32).reshape(-1, 768)

    def f0(self, x16: np.ndarray) -> np.ndarray:
        """Pitch in Hz at 100 frames/s (0 = unvoiced)."""
        t = self.torch.from_numpy(x16.astype(np.float32)).to(self.device)
        return self.rmvpe.infer_from_audio_t(t, threshold=0.03).squeeze().float().cpu().numpy().reshape(-1)
