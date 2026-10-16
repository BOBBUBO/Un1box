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
$identityTest = Join-Path $PSScriptRoot 'runtime/test-ios-file-identity.exe'
if (!(Test-Path $identityTest)) { throw 'Missing NOR file-identity regression test.' }
$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ('LightTouch-NOR-identity-' + [guid]::NewGuid().ToString('N'))
[System.IO.Directory]::CreateDirectory($scratch) | Out-Null
& $identityTest $scratch
if ($LASTEXITCODE -ne 0) { throw "NOR file-identity regression failed; synthetic evidence retained at $scratch" }
[System.IO.Directory]::Delete($scratch, $false)
Write-Output 'Native loader/machine/display smoke PASS. Guest, graphics and audio acceptance remain unqualified.'
