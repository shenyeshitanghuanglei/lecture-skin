# One-click start for the voice changer server (http://127.0.0.1:18888)
# Uses the local proxy if HTTPS_PROXY is set; set HF_ENDPOINT=https://hf-mirror.com to use a mirror instead.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Join-Path $root 'vendor\voice-changer\server')
& (Join-Path $root '.venv\Scripts\python.exe') main.py
