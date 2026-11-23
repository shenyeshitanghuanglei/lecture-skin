# Sweep real-time conversion settings on a test wav and compare each to a high-quality
# reference (big blocks, long context, fp32). Lower mel_dist = closer to the model's best.
param([string]$In = 'tests\audio\male_en.wav', [int]$Tran = 12, [string]$Tag = 'male')
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$py = '.\.venv\Scripts\python.exe'
$out = 'tests\audio\out'; New-Item -ItemType Directory -Force $out | Out-Null
$ref = "$out\${Tag}_ref.wav"
& $py tools\offline_convert.py $In $ref --chunk 375 --crossfade 0.1 --extra 2.0 --fp32 --auto-pitch 0 --tran $Tran 2>$null | Out-Null
$variants = [ordered]@{
  'current(85ms,xf.05,ex.5,fp16)' = @('--chunk', '32', '--crossfade', '0.05', '--extra', '0.5')
  'xf.10'                         = @('--chunk', '32', '--crossfade', '0.10', '--extra', '0.5')
  'extra1.0'                      = @('--chunk', '32', '--crossfade', '0.05', '--extra', '1.0')
  'extra1.0,xf.10'                = @('--chunk', '32', '--crossfade', '0.10', '--extra', '1.0')
  'fp32'                          = @('--chunk', '32', '--crossfade', '0.05', '--extra', '0.5', '--fp32')
  'upstream(512ms,xf.1,ex.5)'     = @('--chunk', '192', '--crossfade', '0.10', '--extra', '0.5')
  'stable(171ms,xf.1,ex1.0)'      = @('--chunk', '64', '--crossfade', '0.10', '--extra', '1.0')
}
foreach ($k in $variants.Keys) {
  $f = "$out\${Tag}_$($k -replace '[^\w.]','_').wav"
  $m = & $py tools\offline_convert.py $In $f --auto-pitch 0 --tran $Tran @($variants[$k]) 2>$null | Select-String '^\{' | ForEach-Object { $_.Line | ConvertFrom-Json }
  $d = (& $py tools\offline_convert.py $f --compare $ref | ConvertFrom-Json).mel_dist_db
  '{0,-32} mel_dist={1,5}  hnr={2,5}  jump={3,5}  peak={4}' -f $k, $d, $m.out.hnr_db, $m.out.boundary_jump_ratio, $m.out.peak
}
