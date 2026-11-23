"""Startup checks of the desktop panel: free-memory probe and model-load failure detection."""
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import desktop  # noqa: E402

MEMORY_FAIL_LOG = """\
2026-11-22 22:57:10,199 INFO     [EmbedderManager] Loading embedder hubert_base
2026-11-22 22:57:10,200 ERROR    [RVCr2] Failed to create pipeline.
Traceback (most recent call last):
  File "onnx\\__init__.py", line 181, in _load_bytes
    content = readable.read()
MemoryError
2026-11-22 22:57:11,676 INFO     [main] The server is listening on http://127.0.0.1:18888/
"""

OK_LOG = """\
2026-11-22 22:41:10,999 INFO     [RVCr2] Initialized.
2026-11-22 22:41:12,713 INFO     [RVCr2] Warm-up done in 1714 ms
2026-11-22 22:41:14,542 INFO     [main] The server is listening on http://127.0.0.1:18888/
"""


class DesktopBootTest(unittest.TestCase):
    def test_model_load_failure_memory(self):
        assert desktop.model_load_failure(MEMORY_FAIL_LOG) == "memory"

    def test_model_load_failure_cuda_oom(self):
        log = "ERROR    [RVCr2] Failed to create pipeline.\nRuntimeError: CUDA error: out of memory\n"
        assert desktop.model_load_failure(log) == "memory"

    def test_model_load_failure_other(self):
        log = "ERROR    [RVCr2] Failed to create pipeline.\nFileNotFoundError: manbo.index\n"
        assert desktop.model_load_failure(log) == "other"

    def test_model_loaded_ok(self):
        assert desktop.model_load_failure(OK_LOG) is None
        assert desktop.model_load_failure("") is None

    def test_free_commit_gb_is_sane(self):
        free = desktop.free_commit_gb()
        if sys.platform == "win32":
            assert free is not None and 0 <= free < 4096
        else:
            assert free is None

    def test_run_log_reads_only_this_run(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = Path(tmp.name) / "server-console.log"
        log.write_text(MEMORY_FAIL_LOG, encoding="utf-8")  # an earlier, failed run
        patcher = mock.patch.object(desktop.Server, "LOG", log)
        patcher.start()
        self.addCleanup(patcher.stop)
        s = desktop.Server()
        assert s.run_log() == ""  # we didn't start a server
        s._log_offset = log.stat().st_size
        with open(log, "a", encoding="utf-8") as f:
            f.write(OK_LOG)
        assert desktop.model_load_failure(s.run_log()) is None

    def test_boot_controls_wait_and_pick(self):
        c = desktop.BootControls()
        assert c.wait(0.01) is None
        threading.Timer(0.05, c.retry).start()
        assert c.wait(2.0) == "retry"
        assert c.wait(0.01) is None  # consumed
        c.open_anyway()
        c.reset()
        assert c.wait(0.01) is None  # reset drops a stale press


if __name__ == "__main__":
    unittest.main()
