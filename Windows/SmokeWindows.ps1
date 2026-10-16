# Run on Windows after extracting the complete preview. No guest/assets needed.
$ErrorActionPreference = 'Stop'
$engine = Join-Path $PSScriptRoot 'runtime/qemu-system-arm.exe'
if (!(Test-Path $engine)) { throw 'Missing native backend runtime.' }
& $engine --version
if ($LASTEXITCODE -ne 0) { throw 'Native backend did not load successfully.' }
$machines = & $engine -machine help
if ($LASTEXITCODE -ne 0) { throw 'Native machine enumeration failed.' }
foreach ($board in @('iPod-Touch ', 'iPod-Touch-4G ')) {
    if (!(($machines -join "`n").Contains($board))) { throw "Missing expected board: $board" }
}
& $engine -display help
if ($LASTEXITCODE -ne 0) { throw 'Display enumeration failed.' }
Write-Output 'Native loader/machine/display smoke PASS. Guest, graphics and audio acceptance remain unqualified.'
