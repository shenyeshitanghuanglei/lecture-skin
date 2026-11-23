"""Unit tests for the voice-cloning trainer pieces that need no GPU or model weights.

Run:  .venv\\Scripts\\python.exe -m unittest tests.test_clone
"""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trainer import audio  # noqa: E402
from trainer.common import HOP, MODEL_CONFIG, SEGMENT, SR  # noqa: E402
from trainer.export import safe_name  # noqa: E402
from trainer.features import coarse_f0  # noqa: E402
from trainer.train import collate, plan_epochs  # noqa: E402


def tone(sec: float, sr: int = SR, hz: float = 200.0, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(sec * sr)) / sr
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def silence(sec: float, sr: int = SR) -> np.ndarray:
    return np.zeros(int(sec * sr), np.float32)


class SliceTest(unittest.TestCase):
    def test_splits_at_pauses(self):
        x = np.concatenate([tone(2.0), silence(0.6), tone(1.5), silence(0.6), tone(2.5)])
        spans = audio.slice_speech(x, SR)
        self.assertEqual(len(spans), 3)
        for (s, e), want in zip(spans, (2.0, 1.5, 2.5)):
            self.assertAlmostEqual((e - s) / SR, want, delta=0.5)

    def test_short_gaps_do_not_split(self):
        x = np.concatenate([tone(1.0), silence(0.1), tone(1.0)])
        self.assertEqual(len(audio.slice_speech(x, SR)), 1)

    def test_long_phrases_are_cut_to_training_length(self):
        spans = audio.slice_speech(tone(20.0), SR)
        self.assertGreater(len(spans), 4)
        self.assertTrue(all((e - s) / SR <= 3.7 + 0.8 + 0.01 for s, e in spans))

    def test_blips_are_dropped(self):
        x = np.concatenate([silence(1.0), tone(0.2), silence(1.0)])
        self.assertEqual(audio.slice_speech(x, SR), [])

    def test_silence_only(self):
        self.assertEqual(audio.slice_speech(silence(3.0), SR), [])


class LevelTest(unittest.TestCase):
    def test_normalize_file_targets_robust_peak(self):
        y = audio.normalize_file(tone(2.0, amp=0.05))
        self.assertAlmostEqual(float(np.percentile(np.abs(y), 99.9)), 0.9, delta=0.02)

    def test_normalize_clip_matches_rvc_blend(self):
        y = audio.normalize_clip(tone(1.0, amp=0.5))
        self.assertAlmostEqual(float(np.abs(y).max()), 0.9 * 0.75 + 0.25 * 0.5, delta=0.01)

    def test_decode_and_resample_wav(self):
        import soundfile as sf

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.wav"
            sf.write(p, tone(1.0, sr=44100), 44100)
            y = audio.decode(p, 16000)
            self.assertAlmostEqual(len(y) / 16000, 1.0, delta=0.02)


class PitchTest(unittest.TestCase):
    def test_coarse_f0_matches_inference_mapping(self):
        f0 = np.array([0.0, 50.0, 1100.0, 220.0, 5000.0])
        c = coarse_f0(f0)
        self.assertEqual(c[0], 1)  # unvoiced
        self.assertEqual(c[1], 1)
        self.assertEqual(c[2], 255)
        self.assertEqual(c[4], 255)  # clipped
        self.assertTrue(1 < c[3] < 255)


class TrainHelpersTest(unittest.TestCase):
    def test_model_config_is_rvc_v2_40k(self):
        self.assertEqual(len(MODEL_CONFIG), 18)
        self.assertEqual(MODEL_CONFIG[0], 1025)
        self.assertEqual(MODEL_CONFIG[1], SEGMENT // HOP)
        self.assertEqual(int(np.prod(MODEL_CONFIG[12])), HOP)  # upsampling = hop size
        self.assertEqual(MODEL_CONFIG[-1], SR)

    def test_short_material_gets_more_epochs(self):
        self.assertGreater(plan_epochs("standard", 240), plan_epochs("standard", 600))
        self.assertEqual(plan_epochs("standard", 600), 200)
        self.assertLessEqual(plan_epochs("standard", 30), 400)
        self.assertGreaterEqual(plan_epochs("standard", 36000), 100)

    def test_collate_pads_to_a_length_bucket(self):
        import torch

        def item(n):
            return (torch.ones(n, 768), torch.ones(n, dtype=torch.long), torch.ones(n), torch.ones(n * HOP))

        phone, pitch, pitchf, wav, lengths = collate([item(40), item(55)])
        self.assertEqual(tuple(phone.shape), (2, 64, 768))  # 55 rounded up to a multiple of 32
        self.assertEqual(wav.shape[1], 64 * HOP)
        self.assertEqual(lengths.tolist(), [40, 55])
        self.assertEqual(float(phone[0, 40:].abs().sum()), 0.0)
        self.assertEqual(float(phone[1, 55:].abs().sum()), 0.0)

    def test_batch_size_follows_memory_budget(self):
        from trainer.train import pick_batch_size

        self.assertEqual(pick_batch_size(7.0), 8)
        self.assertEqual(pick_batch_size(5.0), 6)
        self.assertEqual(pick_batch_size(5.2), 8)
        self.assertEqual(pick_batch_size(3.5), 4)
        self.assertEqual(pick_batch_size(1.0), 2)

    def test_safe_name_is_ascii(self):
        self.assertEqual(safe_name("王老师"), "")
        self.assertEqual(safe_name("Prof Li 2"), "Prof_Li_2")


if __name__ == "__main__":
    unittest.main()
