"""Automatic pitch (transpose) matching — voice-translater addition.

Keeps a rolling window of the *source* speaker's voiced F0 and suggests the
semitone shift that moves its median onto the target voice's typical pitch:

    tran = round(12 * log2(target_hz / source_median_hz))

A suggestion is only made when the ideal shift drifts more than `hysteresis`
semitones from the current one, so the pitch doesn't wobble mid-sentence.
"""
from __future__ import annotations

import math
import time
from collections import deque

import numpy as np

F0_MIN_HZ = 50.0
F0_MAX_HZ = 1100.0


class AutoPitch:
    def __init__(
        self,
        window_s: float = 20.0,
        update_every_s: float = 2.0,
        min_voiced_frames: int = 150,  # ~1.5 s of voiced speech at 100 frames/s
        hysteresis: float = 1.0,
        max_shift: int = 15,  # larger RVC shifts sound strained/hoarse
    ):
        self.window_s = window_s
        self.update_every_s = update_every_s
        self.min_voiced_frames = min_voiced_frames
        self.hysteresis = hysteresis
        self.max_shift = max_shift
        self.reset()

    def reset(self) -> None:
        self._chunks: deque[tuple[float, np.ndarray]] = deque()
        self._last_update = -math.inf

    def observe(self, f0: np.ndarray, now: float | None = None) -> None:
        """Add raw (un-shifted) F0 frames in Hz; unvoiced frames are 0."""
        now = time.monotonic() if now is None else now
        f0 = np.asarray(f0, dtype=np.float32).ravel()
        voiced = f0[(f0 > F0_MIN_HZ) & (f0 < F0_MAX_HZ)]
        if voiced.size:
            self._chunks.append((now, voiced))
        while self._chunks and now - self._chunks[0][0] > self.window_s:
            self._chunks.popleft()

    @property
    def voiced_frames(self) -> int:
        return sum(c.size for _, c in self._chunks)

    def source_median_hz(self) -> float | None:
        if self.voiced_frames < self.min_voiced_frames:
            return None
        return float(np.median(np.concatenate([c for _, c in self._chunks])))

    def ideal_shift(self, target_hz: float) -> float | None:
        src = self.source_median_hz()
        if src is None or target_hz <= 0:
            return None
        return 12.0 * math.log2(target_hz / src)

    def suggest(self, target_hz: float, current: int, now: float | None = None) -> int | None:
        """Return a new semitone shift if one is warranted, else None."""
        now = time.monotonic() if now is None else now
        if now - self._last_update < self.update_every_s:
            return None
        ideal = self.ideal_shift(target_hz)
        if ideal is None:
            return None
        self._last_update = now
        if abs(ideal - current) <= self.hysteresis:
            return None
        new = int(round(max(-self.max_shift, min(self.max_shift, ideal))))
        return new if new != current else None
