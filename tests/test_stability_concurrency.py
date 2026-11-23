"""Unit tests for concurrency, thread-safety, and clone queueing.

Run:  .venv\\Scripts\\python.exe -m unittest tests.test_stability_concurrency
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "vendor" / "voice-changer" / "server"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from fastapi import HTTPException  # noqa: E402
from data.ModelSlot import RVCModelSlot, loadAllSlotInfo, saveSlotInfo  # noqa: E402
from voice_changer.IORecorder import IORecorder  # noqa: E402
from voice_changer.ModelSlotManager import ModelSlotManager  # noqa: E402
from voice_changer.utils.Timer import Timer2  # noqa: E402


class TimerAndRecorderTest(unittest.TestCase):
    def test_timer2_tracks_total_elapsed_across_laps(self):
        with Timer2("unit-test-timer", True) as t:
            time.sleep(0.01)
            t.record("lap1")
            time.sleep(0.01)
            t.record("lap2")
        self.assertGreaterEqual(t.secs, 0.015)

    def test_io_recorder_is_lazy_until_write(self):
        import voice_changer.IORecorder as ior

        with tempfile.TemporaryDirectory() as td:
            in_wav = os.path.join(td, "in.wav")
            out_wav = os.path.join(td, "out.wav")
            with mock.patch.object(ior, "TMP_DIR", td), \
                 mock.patch.object(ior, "STREAM_INPUT_FILE", in_wav), \
                 mock.patch.object(ior, "STREAM_OUTPUT_FILE", out_wav):
                rec = IORecorder(48000, 48000)
                rec.open(44100, 44100)
                self.assertFalse(os.path.exists(in_wav))
                self.assertFalse(os.path.exists(out_wav))
                rec.write_input(b"\x00\x00" * 160)
                self.assertTrue(os.path.exists(in_wav))
                rec.close()


class SlotConcurrencyAndResilienceTest(unittest.TestCase):
    def test_concurrent_slot_allocation_is_unique(self):
        with tempfile.TemporaryDirectory() as td:
            msm = ModelSlotManager(td)
            allocated: list[int] = []
            lk = threading.Lock()

            def worker():
                s = msm.allocate_slot()
                with lk:
                    allocated.append(s)

            threads = [threading.Thread(target=worker) for _ in range(25)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            self.assertEqual(len(allocated), 25)
            self.assertEqual(len(set(allocated)), 25)

    def test_corrupt_params_json_does_not_crash_startup(self):
        with tempfile.TemporaryDirectory() as td:
            good = RVCModelSlot(name="测试声音", defaultProtect=0.33, version="v1")
            saveSlotInfo(td, 3, good)
            bad_dir = os.path.join(td, "7")
            os.makedirs(bad_dir, exist_ok=True)
            Path(bad_dir, "params.json").write_text("{corrupt-json", encoding="utf-8")

            slots = loadAllSlotInfo(td)
            self.assertEqual(slots[3].name, "测试声音")
            self.assertEqual(slots[3].version, "v1")
            self.assertIsNone(slots[7].voiceChangerType)

            msm = ModelSlotManager(td)
            self.assertIsNone(msm.get_slot_info(-1))
            self.assertIsNone(msm.get_slot_info(-2))
            self.assertIsNone(msm.get_slot_info(999))


class CloneAvailabilityTest(unittest.TestCase):
    def test_routes_and_panel_status_follow_server_setting(self):
        import restapi.MMVC_Rest as rest
        from settings import ServerSettings
        from voice_changer.VoiceChangerSettings import VoiceChangerSettings
        from vt.api import VtApi

        manager = mock.Mock(
            settings=VoiceChangerSettings(),
            server_audio=SimpleNamespace(live={}, running=False),
            vc=None,
            devices=[],
        )
        manager.modelSlotManager.get_slot_info.return_value = None
        for enabled in (False, True):
            with self.subTest(enabled=enabled), mock.patch.object(rest.MMVC_Rest, "_instance", None), \
                 mock.patch.object(rest, "TMP_DIR", str(ROOT)), \
                 mock.patch.object(rest, "get_settings", return_value=ServerSettings(
                     _env_file=None, model_dir=str(ROOT), VT_ENABLE_CLONE=enabled,
                 )), \
                 mock.patch.object(VtApi, "_check_system_default"), \
                 mock.patch.object(VtApi, "_apply_quality_defaults"), \
                 mock.patch.object(VtApi, "_bypass"), \
                 mock.patch.object(VtApi, "_system_info", return_value={}):
                app = rest.MMVC_Rest.get_instance(manager)
                paths = app.openapi()["paths"]
                self.assertEqual("/vt/api/clone/jobs" in paths, enabled)
                self.assertIn("/vt/api/voices/import", paths)
                self.assertIn("/vt/api/status", paths)
                self.assertIs(VtApi(manager, clone_enabled=enabled).status()["cloneEnabled"], enabled)


class CloneQueueConcurrencyTest(unittest.TestCase):
    def test_multi_job_queue_and_prepare_pauses_conversion(self):
        import trainer.common as tc
        from vt import clone_api as ca

        with tempfile.TemporaryDirectory() as td:
            jobs_dir = Path(td) / "clone_jobs"
            jobs_dir.mkdir(parents=True, exist_ok=True)

            class FakeVt:
                class S:
                    serverAudioStated = 1
                    modelSlotIndex = -1

                s = S()
                stop_called = 0

                def stop(self):
                    self.stop_called += 1
                    self.s.serverAudioStated = 0

            fake_vt = FakeVt()
            alive_pids: set[int] = set()
            next_pid = [1000]

            def fake_spawn(self_api, job, *args):
                next_pid[0] += 1
                pid = next_pid[0]
                alive_pids.add(pid)
                job.report(pid=pid)

            with mock.patch.object(tc, "JOBS", jobs_dir), \
                 mock.patch.object(ca, "JOBS", jobs_dir), \
                 mock.patch.object(ca, "_pid_alive", side_effect=lambda p: p in alive_pids), \
                 mock.patch.object(ca.CloneApi, "_spawn", fake_spawn):
                ca._queue.clear()
                api = ca.CloneApi(fake_vt, enable_dispatcher=False)

                j1 = api.create_job(ca.CreateBody(name="声音A"))["id"]
                j2 = api.create_job(ca.CreateBody(name="声音B"))["id"]
                tc.Job(j1).update_meta(files=[{"name": "a.wav", "label": "a.wav", "kind": "audio", "separate": False}])
                tc.Job(j2).update_meta(files=[{"name": "b.wav", "label": "b.wav", "kind": "audio", "separate": False}])

                # Job 1 starts immediately on GPU and pauses active real-time conversion
                r1 = api.prepare(j1)
                self.assertEqual(r1["progress"]["state"], "preparing")
                self.assertEqual(fake_vt.stop_called, 1)

                # Calling prepare twice on the SAME job still raises 409
                with self.assertRaises(HTTPException) as ctx:
                    api.prepare(j1)
                self.assertEqual(ctx.exception.status_code, 409)

                # Calling prepare on Job 2 while Job 1 holds GPU enqueues Job 2 instead of failing!
                r2 = api.prepare(j2)
                self.assertEqual(r2["progress"]["state"], "queued")
                self.assertTrue(ca.training_active())

                # When Job 1 finishes, dispatcher automatically starts Job 2
                alive_pids.clear()
                tc.Job(j1).report(state="prepared", running=None, pid=None)
                self.assertTrue(api._dispatch_once())
                self.assertEqual(api.get_job(j2)["progress"]["state"], "preparing")

                # Clean up
                alive_pids.clear()
                ca._queue.clear()


if __name__ == "__main__":
    unittest.main()
