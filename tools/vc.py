"""Tiny CLI for the running voice changer server (bypasses the local proxy).

  python tools/vc.py info                 # key settings
  python tools/vc.py set key=value ...    # update settings, e.g. tran=10 serverReadChunkSize=64
  python tools/vc.py start | stop         # start/stop server audio
"""
import json
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:18888"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
KEYS = [
    "serverAudioStated", "gpu", "modelSlotIndex", "serverInputDeviceId", "serverOutputDeviceId",
    "serverInputAudioSampleRate", "serverOutputAudioSampleRate", "serverReadChunkSize",
    "extraConvertSize", "crossFadeOverlapSize", "tran", "autoPitch", "autoPitchTargetHz",
    "f0Detector", "silentThreshold", "passThrough",
]


def update(key: str, val) -> None:
    data = urllib.parse.urlencode({"key": key, "val": val}).encode()
    OPENER.open(urllib.request.Request(BASE + "/update_settings", data=data), timeout=300).read()


def info() -> dict:
    return json.loads(OPENER.open(BASE + "/info", timeout=30).read())


def main() -> None:
    cmd, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("info", [])
    if cmd == "set":
        for kv in args:
            k, v = kv.split("=", 1)
            update(k, v)
    elif cmd == "start":
        update("serverAudioStated", 1)
    elif cmd == "stop":
        update("serverAudioStated", 0)
    i = info()
    print({k: i.get(k) for k in KEYS})


if __name__ == "__main__":
    main()
