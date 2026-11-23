# Restart the voice changer server in its own minimized window and wait until it answers.
# Optional: -Start to also start audio afterwards.
param([switch]$Start)
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'main\.py' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep 1
Start-Process powershell -ArgumentList '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $root 'start.ps1') -WindowStyle Minimized | Out-Null
for ($i = 0; $i -lt 120; $i++) {
    try { $null = Invoke-WebRequest http://127.0.0.1:18888/api/hello -UseBasicParsing -TimeoutSec 2 -Proxy $null; "server up after $i s"; break }
    catch { Start-Sleep 1 }
}
if ($Start) { & (Join-Path $root '.venv\Scripts\python.exe') (Join-Path $root 'tools\vc.py') start | Out-Null; "audio started" }
