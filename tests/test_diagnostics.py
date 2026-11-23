import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import diagnostics as dg  # noqa: E402

MASKS = [(r"C:\Users\alice", "%USERPROFILE%"), ("alice", "<user>")]


class RedactTest(unittest.TestCase):
    def test_home_and_username_are_masked_case_insensitively(self):
        text = r"Traceback: c:\users\ALICE\Desktop\x.py; owner=Alice"
        self.assertEqual(dg.redact(text, MASKS), r"Traceback: %USERPROFILE%\Desktop\x.py; owner=<user>")


class BundleTest(unittest.TestCase):
    def test_bundle_contains_logs_settings_and_live_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            logs = tmp / "logs"
            logs.mkdir()
            (logs / "server-console.log").write_text("start\n" + "x" * 50 + "\nboom at C:\\Users\\alice\\a.py\n",
                                                     encoding="utf-8")
            (logs / "notes.txt").write_text("not a log", encoding="utf-8")
            setting = tmp / "vt_devices.json"
            setting.write_text('{"input": {"name": "CABLE Output"}}', encoding="utf-8")

            with mock.patch.object(dg, "_masks", return_value=MASKS), \
                    mock.patch.object(dg, "system_info", return_value={"version": {"app": "t"}}), \
                    mock.patch.object(dg, "LOG_TAIL_BYTES", 40):
                path = dg.build_bundle({"status": {"running": True}}, out_dir=tmp / "out",
                                       log_dir=logs, setting_files=[setting, tmp / "missing.json"])

            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
                self.assertEqual(names, {"system.json", "live/status.json", "logs/server-console.log",
                                         "settings/vt_devices.json", "README.txt"})
                log = z.read("logs/server-console.log").decode()
                self.assertIn("boom at %USERPROFILE%\\a.py", log)
                self.assertNotIn("start", log)  # only the tail is kept
                self.assertTrue(json.loads(z.read("live/status.json"))["running"])

    def test_old_bundles_are_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for i in range(dg.KEEP_BUNDLES + 3):
                (out / f"old{i}.zip").write_bytes(b"")
            dg._prune(out)
            self.assertEqual(len(list(out.glob("*.zip"))), dg.KEEP_BUNDLES)


if __name__ == "__main__":
    unittest.main()
