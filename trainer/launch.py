"""Start ``python -m trainer.run ...`` outside the caller's process tree, then exit.

The desktop panel stops the server with ``taskkill /T`` when its window closes; a
trainer started directly by the server would die with it. Starting it from this
short-lived middle process leaves it orphaned (no live parent), so training keeps
going after the panel is closed. The trainer reports its own pid in progress.json.

    python -m trainer.launch <logfile> <trainer.run args...>
"""
from __future__ import annotations

import os
import subprocess
import sys

from trainer.common import ROOT


def main() -> int:
    log, args = sys.argv[1], sys.argv[2:]
    flags = 0
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        flags = 0x00000008 | 0x00000200 | 0x08000000
    env = {**os.environ, "VT_LOG_REDIRECTED": "1", "PYTHONIOENCODING": "utf-8"}
    with open(log, "ab") as out:
        p = subprocess.Popen([sys.executable, "-m", "trainer.run", *args], cwd=str(ROOT), stdout=out,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags,
                             close_fds=True, env=env)
    print(p.pid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
