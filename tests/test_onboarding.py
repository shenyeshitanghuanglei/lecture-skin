import subprocess
import sys
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor" / "voice-changer" / "server"))
from vt import onboarding  # noqa: E402

D = namedtuple("D", "index name hostAPI")


class OnboardingTest(unittest.TestCase):
    def test_check_vb_cable_detected(self):
        inputs = [
            D(1, "Microphone (Realtek)", "Windows WASAPI"),
            D(2, "CABLE Output (VB-Audio Virtual Cable)", "Windows WASAPI"),
        ]
        res = onboarding.check_vb_cable(inputs)
        self.assertTrue(res["installed"])
        self.assertIn("CABLE Output", res["deviceName"])
        self.assertEqual(res["downloadUrl"], onboarding.ALLOWED_URLS["vb_cable"])

    def test_check_vb_cable_missing(self):
        res = onboarding.check_vb_cable([D(1, "Microphone", "Windows WASAPI")])
        self.assertFalse(res["installed"])
        self.assertIsNone(res["deviceName"])

    def test_open_allowed_url_rejects_unknown_target(self):
        with self.assertRaises(ValueError):
            onboarding.open_allowed_url("https://evil.example.com")

    def test_open_allowed_url_accepts_vb_cable(self):
        if sys.platform == "win32":
            with mock.patch("os.startfile") as m_open:
                url = onboarding.open_allowed_url("vb_cable")
                self.assertEqual(url, onboarding.ALLOWED_URLS["vb_cable"])
                m_open.assert_called_once_with(onboarding.ALLOWED_URLS["vb_cable"])
        else:
            with mock.patch("webbrowser.open") as m_open:
                url = onboarding.open_allowed_url("vb_cable")
                self.assertEqual(url, onboarding.ALLOWED_URLS["vb_cable"])
                m_open.assert_called_once_with(onboarding.ALLOWED_URLS["vb_cable"])

    def test_env_installer_skips_when_uv_and_python_ready(self):
        installer = onboarding.EnvInstaller()
        with (
            mock.patch.object(onboarding, "find_uv", return_value="C:/bin/uv.exe"),
            mock.patch.object(onboarding, "check_python", return_value={"installed": True, "ok": True, "version": "3.12.9"}),
            mock.patch.object(installer, "_run_cmd") as m_run,
        ):
            installer._worker()
            m_run.assert_not_called()
            st = installer.status()
            self.assertEqual(st["status"], "success")

    def test_env_installer_auto_installs_missing_uv_and_python(self):
        installer = onboarding.EnvInstaller()
        with (
            mock.patch.object(onboarding, "find_uv", side_effect=[None, "C:/Users/me/.local/bin/uv.exe"]),
            mock.patch.object(onboarding, "check_python", return_value={"installed": True, "ok": False, "version": "3.11.8"}),
            mock.patch.object(installer, "_run_cmd", return_value=0) as m_run,
        ):
            installer._worker()
            self.assertEqual(m_run.call_count, 2)
            # First call installs uv, second installs Python 3.12 via uv
            self.assertEqual(m_run.call_args_list[1].args[0], ["C:/Users/me/.local/bin/uv.exe", "python", "install", "3.12"])
            self.assertEqual(installer.status()["status"], "success")

    def test_env_installer_reports_error_on_uv_install_failure(self):
        installer = onboarding.EnvInstaller()
        with (
            mock.patch.object(onboarding, "find_uv", return_value=None),
            mock.patch.object(installer, "_run_cmd", return_value=1),
        ):
            installer._worker()
            st = installer.status()
            self.assertEqual(st["status"], "error")
            self.assertIn("uv", st["message"])

    def test_env_installer_reports_timeout_without_raw_command(self):
        installer = onboarding.EnvInstaller()
        with (
            mock.patch.object(onboarding, "find_uv", return_value=None),
            mock.patch.object(installer, "_run_cmd", side_effect=subprocess.TimeoutExpired("raw-command", 1)),
        ):
            installer._worker()
        self.assertEqual(installer.status()["status"], "error")
        self.assertIn("超时", installer.status()["message"])
        self.assertNotIn("raw-command", str(installer.status()))

    def test_install_endpoint_requires_loopback_host(self):
        from fastapi import HTTPException, Request
        from vt.api import VtApi

        api = VtApi.__new__(VtApi)
        api._env_installer = mock.Mock()
        api._build_onboarding_env = mock.Mock(return_value={})
        scope = {"type": "http", "path": "/vt/api/onboarding/ensure-env"}
        request = Request({**scope, "headers": [(b"host", b"example.com:18888")]})
        with self.assertRaises(HTTPException) as caught:
            api.onboarding_ensure_env(request)
        self.assertEqual(caught.exception.status_code, 403)
        api._env_installer.ensure_async.assert_not_called()
        request = Request({**scope, "headers": [(b"host", b"localhost:18888")]})
        self.assertEqual(api.onboarding_ensure_env(request), {})
        api._env_installer.ensure_async.assert_called_once()


if __name__ == "__main__":
    unittest.main()
