import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor" / "voice-changer" / "server"))
from voice_changer.common.AutoPitch import AutoPitch  # noqa: E402


def frames(hz: float, n: int = 100, unvoiced: int = 0) -> np.ndarray:
    rng = np.random.default_rng(0)
    voiced = hz * 2 ** (rng.normal(0, 1.5, n) / 12)  # ~1.5 semitone natural spread
    return np.concatenate([voiced, np.zeros(unvoiced)])


class AutoPitchTest(unittest.TestCase):
    def test_male_to_female_shifts_up_about_an_octave(self):
        ap = AutoPitch()
        for t in range(3):
            ap.observe(frames(120, unvoiced=50), now=float(t))
        self.assertEqual(ap.suggest(target_hz=260, current=0, now=3.0), 13)  # 12*log2(260/120)=13.4

    def test_female_teacher_close_to_target_is_left_alone(self):
        ap = AutoPitch()
        for t in range(3):
            ap.observe(frames(250), now=float(t))
        self.assertIsNone(ap.suggest(target_hz=260, current=0, now=3.0))  # ideal 0.68 st

    def test_female_teacher_slightly_low_gets_one_semitone(self):
        ap = AutoPitch()
        for t in range(3):
            ap.observe(frames(240), now=float(t))
        self.assertEqual(ap.suggest(target_hz=260, current=0, now=3.0), 1)  # ideal 1.39 st

    def test_waits_for_enough_voiced_speech(self):
        ap = AutoPitch()
        ap.observe(frames(120, n=50), now=0.0)
        self.assertIsNone(ap.suggest(260, 0, now=3.0))

    def test_hysteresis_keeps_current_shift(self):
        ap = AutoPitch()
        for t in range(3):
            ap.observe(frames(120), now=float(t))
        self.assertIsNone(ap.suggest(260, current=13, now=3.0))

    def test_cooldown_between_updates(self):
        ap = AutoPitch()
        for t in range(3):
            ap.observe(frames(120), now=float(t))
        self.assertEqual(ap.suggest(260, 0, now=3.0), 13)
        self.assertIsNone(ap.suggest(260, 0, now=3.5))

    def test_old_frames_expire_when_speaker_changes(self):
        ap = AutoPitch(window_s=10)
        for t in range(3):
            ap.observe(frames(120), now=float(t))
        for t in range(20, 23):
            ap.observe(frames(220), now=float(t))
        self.assertAlmostEqual(ap.source_median_hz(), 220, delta=10)

    def test_ignores_out_of_range_and_unvoiced(self):
        ap = AutoPitch(min_voiced_frames=1)
        ap.observe(np.array([0, 0, 20, 5000, 200], dtype=np.float32), now=0.0)
        self.assertEqual(ap.voiced_frames, 1)


if __name__ == "__main__":
    unittest.main()
