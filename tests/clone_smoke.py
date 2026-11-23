"""End-to-end smoke test for /vt/api/clone against a running server.

Run:  .venv\\Scripts\\python.exe tests\\clone_smoke.py SPEECH [VIDEO]
  SPEECH  >= 3 min of one speaker (any audio format) you are allowed to use
  VIDEO   optional audio/video file with background music, to exercise vocal separation
Trains for only a few epochs, adds the result to the voice library, checks it is
there, then deletes both the voice and the job.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:18888/vt/api"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method, path, body=None, raw=None, ctype=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": ctype or "application/json"} if data is not None else {}
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with OPENER.open(req, timeout=600) as r:
            body = r.read()
            return r.status, (json.loads(body) if body and r.headers.get_content_type() == "application/json" else body)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def upload(job, paths, kind="file"):
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="kind"\r\n\r\n{kind}\r\n'.encode()]
    for p in paths:
        with open(p, "rb") as f:
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="files"; filename="{os.path.basename(p)}"\r\n'
                         f"Content-Type: application/octet-stream\r\n\r\n".encode() + f.read() + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return call("POST", f"/clone/jobs/{job}/files", raw=b"".join(parts), ctype=f"multipart/form-data; boundary={b}")


def wait(job, until, timeout=1800):
    t = time.time()
    while time.time() - t < timeout:
        s, j = call("GET", f"/clone/jobs/{job}")
        st = j["progress"]["state"]
        if st in until and not j["progress"].get("running"):
            return j
        if st in ("failed", "interrupted"):
            raise AssertionError(f"job {st}: {j['progress'].get('error')}\n{j['log']}")
        time.sleep(2)
    raise AssertionError("timeout")


def main():
    speech = sys.argv[1]
    video = sys.argv[2] if len(sys.argv) > 2 else None
    s, j = call("POST", "/clone/jobs", {"name": "冒烟测试"})
    assert s == 200, j
    jid = j["id"]
    slot = None
    try:
        s, j = upload(jid, [speech] + ([video] if video else []))
        assert s == 200 and len(j["meta"]["files"]) == (2 if video else 1), j
        if video:
            vf = j["meta"]["files"][1]
            assert vf["separate"] == (vf["kind"] == "video")
            call("PATCH", f"/clone/jobs/{jid}/files/{vf['name']}", {"separate": True})
        s, j = call("POST", f"/clone/jobs/{jid}/train", {"preset": "fast", "consent": True})
        assert s == 400, "training must require prepared material"
        s, j = call("POST", f"/clone/jobs/{jid}/prepare")
        assert s == 200, j
        s, _ = call("POST", f"/clone/jobs/{jid}/prepare")
        assert s == 409, "second prepare while running must be refused"
        j = wait(jid, {"prepared"})
        rep = j["report"]
        print("report:", rep)
        assert rep["canTrain"], rep
        clip = j["clips"][0]["id"]
        s, wav = call("GET", f"/clone/jobs/{jid}/clips/{clip}.wav")
        assert s == 200 and wav[:4] == b"RIFF"
        s, j = call("PATCH", f"/clone/jobs/{jid}/clips", {"ids": [clip], "excluded": True})
        assert j["report"]["used"] == rep["used"] - 1
        s, j = call("POST", f"/clone/jobs/{jid}/train", {"preset": "fast"})
        assert s == 400, "consent is required"
        s, j = call("POST", f"/clone/jobs/{jid}/train", {"preset": "fast", "consent": True, "epochs": 6})
        assert s == 200, j
        s, st = call("POST", "/start")
        assert s == 409, f"voice changer must not start while training: {s} {st}"
        j = wait(jid, {"trained"})
        cps = j["progress"]["checkpoints"]
        print("checkpoints:", cps)
        assert cps and cps[-1]["epoch"] == 6
        if cps[-1]["preview"]:
            s, wav = call("GET", f"/clone/jobs/{jid}/preview/{cps[-1]['preview']}")
            assert s == 200 and wav[:4] == b"RIFF"
        s, r = call("POST", f"/clone/jobs/{jid}/finalize", {"epoch": 6, "name": "冒烟测试声音"})
        assert s == 200, r
        slot = r["slot"]
        v = next(x for x in r["voices"] if x["slot"] == slot)
        assert v["name"] == "冒烟测试声音" and v["hasIndex"] and v["targetPitchHz"] > 0, v
        print("voice:", v)
    finally:
        if slot is not None:
            call("DELETE", f"/voices/{slot}")
        call("POST", f"/clone/jobs/{jid}/cancel")
        time.sleep(3)
        s, _ = call("DELETE", f"/clone/jobs/{jid}")
        print("cleanup:", s)
    print("OK")


if __name__ == "__main__":
    main()
