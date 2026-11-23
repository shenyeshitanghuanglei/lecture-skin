"""Find a voice model's comfortable target pitch: convert test speech at several targets
and report hoarseness (HNR, higher = cleaner). Usage: python tools/pitch_sweep.py SLOT"""
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
SOURCES = {"female_zh": 207, "male_en": 91}  # median F0 of the test clips (pyin)
TARGETS = [200, 230, 260, 290, 320, 350, 380]


def run(src: str, tran: int, slot: int, out: Path) -> dict:
    p = subprocess.run(
        [PY, str(ROOT / "tools" / "offline_convert.py"), str(ROOT / "tests" / "audio" / f"{src}.wav"), str(out),
         "--slot", str(slot), "--auto-pitch", "0", "--tran", str(tran), "--index-ratio", "0.5",
         "--crossfade", "0.1", "--extra", "1.0"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    line = next((l for l in p.stdout.splitlines() if l.startswith("{")), None)
    if line is None:
        raise RuntimeError(p.stderr[-800:])
    return json.loads(line)["out"]


def main() -> None:
    slot = int(sys.argv[1])
    outdir = ROOT / "tests" / "audio" / "out"
    outdir.mkdir(parents=True, exist_ok=True)
    for src, hz in SOURCES.items():
        for t in TARGETS:
            tran = round(12 * math.log2(t / hz))
            if abs(tran) > 15:
                continue
            m = run(src, tran, slot, outdir / f"slot{slot}_{src}_{t}.wav")
            print(f"{src:10s} target={t}Hz tran={tran:+3d}  hnr={m['hnr_db']:5.1f}  rms={m['rms_dbfs']}", flush=True)


if __name__ == "__main__":
    main()
