"""Smoke test for the /vt/api control-panel endpoints against a running server.

Run:  .venv\\Scripts\\python.exe tests\\api_smoke.py <path\\to\\model.pth>
(or set VT_TEST_MODEL); use any RVC .pth you are allowed to use.
It restores every setting it touches and deletes the voices it imports.
"""
import json
import os
import pickle
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:18888/vt/api"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
MODEL = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("VT_TEST_MODEL", "")


def call(method, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": "application/json"} if body is not None else {}
    h.update(headers or {})
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with OPENER.open(req, timeout=300) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def multipart(fields: dict, files: dict):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (fname, content) in files.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n".encode() + content + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


class Evil:
    def __reduce__(self):
        return (print, ("PWNED: pickle code ran",))


failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def main():
    code, st = call("GET", "/status")
    check("status 200", code == 200, st)
    orig = {"tran": st["pitch"]["tran"], "autoPitch": st["pitch"]["auto"], "targetHz": st["pitch"]["targetHz"],
            "preset": st["perf"]["preset"], "voice": st["voice"]["slot"] if st["voice"] else None}
    check("status has perf/pitch/audio", all(k in st for k in ("perf", "pitch", "audio", "gpu")))

    for _ in range(10):  # first blocks after a fresh start are warm-up
        time.sleep(0.5)
        code, st = call("GET", "/status")
        if not st["running"] or st["perf"]["procMs"] is not None:
            break
    check("live perf present while running", (not st["running"]) or st["perf"]["procMs"] is not None, st["perf"])

    code, st = call("POST", "/settings", {"values": {"autoPitch": False, "tran": 7}})
    check("settings: bool + int", code == 200 and st["pitch"]["auto"] is False and st["pitch"]["tran"] == 7, st.get("pitch"))
    code, _ = call("POST", "/settings", {"values": {"serverInputDeviceId": 3}})
    check("settings rejects non-whitelisted key", code == 400)
    code, st = call("POST", "/settings", {"values": {"passThrough": True}})
    check("passThrough on", code == 200 and st["passThrough"] is True)
    call("POST", "/settings", {"values": {"passThrough": False}})

    code, st = call("POST", "/latency", {"preset": "stable"})
    check("latency preset stable", code == 200 and st["perf"]["preset"] == "stable" and st["perf"]["blockMs"] > 150, st["perf"])
    code, _ = call("POST", "/latency", {"preset": "nope"})
    check("latency rejects unknown", code == 400)

    code, dv = call("GET", "/devices")
    check("devices lists cable", code == 200 and any(d["isCable"] for d in dv["inputs"]), "")
    check("devices WASAPI first", "WASAPI" in dv["inputs"][0]["hostApi"], dv["inputs"][0])

    # import a real model
    with open(MODEL, "rb") as f:
        body, h = multipart({"name": "测试导入", "targetPitchHz": "280"}, {"model": (os.path.basename(MODEL), f.read())})
    code, res = call("POST", "/voices/import", raw=body, headers=h)
    check("import real .pth", code == 200 and "slot" in res, res)
    new_slot = res.get("slot")
    v = next((x for x in res.get("voices", []) if x["slot"] == new_slot), None)
    check("imported voice keeps name + target", v is not None and v["name"] == "测试导入" and v["targetPitchHz"] == 280, v)

    # malicious pickle must be rejected (weights_only=True)
    with tempfile.TemporaryDirectory() as td:
        evil = os.path.join(td, "evil.pth")
        with open(evil, "wb") as f:
            pickle.dump({"config": Evil()}, f)
        with open(evil, "rb") as f:
            body, h = multipart({}, {"model": ("evil.pth", f.read())})
    code, res = call("POST", "/voices/import", raw=body, headers=h)
    check("malicious .pth rejected", code == 400, res)
    code, res = call("POST", "/voices/import", raw=multipart({}, {"model": ("x.exe", b"MZ")})[0],
                     headers=multipart({}, {"model": ("x.exe", b"MZ")})[1])
    check("wrong extension rejected", code in (400, 422), res)

    if new_slot is not None:
        code, vs = call("POST", "/voices/select", {"slot": new_slot})
        check("select imported voice", code == 200 and vs["current"] == new_slot, vs.get("current"))
        code, st = call("GET", "/status")
        check("target pitch follows voice", st["pitch"]["targetHz"] == 280, st["pitch"])
        code, _ = call("DELETE", f"/voices/{new_slot}")
        check("cannot delete active voice", code == 400)
        code, vs = call("PATCH", f"/voices/{new_slot}", {"name": "改名后", "targetPitchHz": 250})
        check("rename + retarget", code == 200 and any(x["name"] == "改名后" for x in vs["voices"]))
        if orig["voice"] is not None:
            call("POST", "/voices/select", {"slot": orig["voice"]})
        code, vs = call("DELETE", f"/voices/{new_slot}")
        check("delete voice", code == 200 and all(x["slot"] != new_slot for x in vs["voices"]))

    # restore
    call("POST", "/settings", {"values": {"autoPitch": orig["autoPitch"], "tran": orig["tran"], "autoPitchTargetHz": orig["targetHz"]}})
    if orig["preset"]:
        call("POST", "/latency", {"preset": orig["preset"]})
    print(f"\n{len(failures)} failure(s)" + (": " + ", ".join(failures) if failures else ""))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
