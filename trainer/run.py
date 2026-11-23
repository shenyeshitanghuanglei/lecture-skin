"""Command line entry: ``python -m trainer.run <stage> --job <id>``.

Stages:
  prepare   raw files -> clips + features + report
  train     fine-tune (``--preset fast|standard|fine`` or ``--epochs N``; resumes unless ``--fresh``)
  export    ``--epoch N``: write export/<name>.pth + index
The process writes progress.json / log.txt in the job directory; the server only reads them.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

from trainer.common import SR16, Job

PREVIEW_TEXT = (
    "同学们好，今天我们来讲一下这一章的重点内容。"
    "首先回顾一下上节课的结论，然后通过两个例子，看看它在实际问题里是怎么用的。"
)


def make_preview_source(job: Job) -> None:
    """Preview sentence: the user's own test clip if given, else Windows' built-in TTS voice."""
    import soundfile as sf

    from trainer import audio

    out = job.path("preview_src.wav")
    custom = job.meta.get("previewFile")
    if custom and job.path("raw", custom).exists():
        try:
            x = audio.decode(job.path("raw", custom), SR16)[: SR16 * 12]
            if len(x) > SR16 // 2:
                sf.write(out, audio.normalize_file(x) * 0.8, SR16, subtype="PCM_16")
                return
        except Exception as e:
            job.log(f"custom preview decode failed ({custom}), falling back to TTS: {e!r}")
    if out.exists():
        return
    if sys.platform != "win32":
        return
    with tempfile.TemporaryDirectory() as d:
        wav = os.path.join(d, "tts.wav")
        ps = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "$v = $s.GetInstalledVoices() | ? { $_.VoiceInfo.Culture.Name -eq 'zh-CN' } | select -First 1;"
            "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) };"
            f"$s.SetOutputToWaveFile('{wav}'); $s.Speak($env:VT_TTS_TEXT); $s.Dispose()"
        )
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=60,
                           env={**os.environ, "VT_TTS_TEXT": PREVIEW_TEXT}, creationflags=0x08000000)
            x = audio.decode(wav, SR16)
            sf.write(out, audio.normalize_file(x) * 0.8, SR16, subtype="PCM_16")
        except Exception as e:  # previews are optional
            job.log(f"preview TTS failed: {e!r}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["prepare", "train", "export"])
    ap.add_argument("--job", required=True)
    ap.add_argument("--preset", default="standard")
    ap.add_argument("--epochs", type=int)
    ap.add_argument("--epoch", type=int)
    ap.add_argument("--fresh", action="store_true")
    a = ap.parse_args(argv)

    job = Job(a.job)
    job.path("cancel").unlink(missing_ok=True)
    job.report(pid=os.getpid(), running=a.stage)
    try:
        if a.stage == "prepare":
            from trainer.prepare import prepare

            prepare(job)
        elif a.stage == "train":
            from trainer.export import build_index
            from trainer.train import train

            make_preview_source(job)
            train(job, a.preset, a.epochs, resume=not a.fresh)
            job.report(message="建立检索索引")
            build_index(job)
            job.report(message="训练完成，请试听并选择一个版本")
        elif a.stage == "export":
            from trainer.export import export

            print(export(job, a.epoch))
        return 0
    except BaseException as e:  # noqa: BLE001 - report everything, including KeyboardInterrupt
        job.fail(e)
        return 1
    finally:
        job.report(pid=None, running=None)


if __name__ == "__main__":
    sys.exit(main())
