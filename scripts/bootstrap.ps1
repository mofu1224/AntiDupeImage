param([Parameter(ValueFromRemainingArguments = $true)][string[]]$LaunchArgs)
$ErrorActionPreference = 'Stop'
# A PowerShell 7 parent can pass a module path incompatible with Windows PowerShell.
$env:PSModulePath = Join-Path $PSHOME 'Modules'
if ($LaunchArgs -contains '--help' -or $LaunchArgs -contains '-h') {
    'Usage: AntiDupeImage.bat [--setup-only] [--offline] [--ml | --cuda | cpu]'
    'Downloads verified local runtimes on first setup. Stop with Ctrl+C.'
    exit 0
}
$root = Split-Path -Parent $PSScriptRoot
$local = Join-Path $root '.portable'
$manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'portable-runtime.json') -Raw | ConvertFrom-Json
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
    throw 'The Windows portable launcher supports x64 Windows only.'
}
$uvDir = Join-Path $local "tools\uv-$($manifest.uv_version)"
$uv = Join-Path $uvDir 'uv.exe'
$tempDir = Join-Path $local 'cache\temp'
[IO.Directory]::CreateDirectory($tempDir) > $null
$env:TEMP = $tempDir
$env:TMP = $tempDir
$env:UV_CACHE_DIR = Join-Path $local 'cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $local 'python'
$env:UV_PYTHON_BIN_DIR = Join-Path $local 'bin'
$env:UV_NO_CONFIG = '1'
Remove-Item Env:UV_PYTHON_PREFERENCE -ErrorAction SilentlyContinue
$env:UV_LINK_MODE = 'copy'
if (-not (Test-Path -LiteralPath $uv)) {
    if ($LaunchArgs -contains '--offline') { throw 'uv is missing. First setup requires internet.' }
    $entry = $manifest.uv.'windows-x64'
    $archive = Join-Path $tempDir $entry.archive
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 180 -Uri "https://github.com/astral-sh/uv/releases/download/$($manifest.uv_version)/$($entry.archive)" -OutFile $archive
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) {
        Remove-Item -LiteralPath $archive
        throw 'uv archive SHA-256 mismatch.'
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $uvDir -Force
    Remove-Item -LiteralPath $archive
}
$installArgs = @('python', 'install', $manifest.python_version, '--no-bin', '--no-registry')
if ($LaunchArgs -contains '--offline') { $installArgs += '--offline' }
& $uv @installArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$uvArgs = @('run', '--no-project', '--managed-python', '--no-python-downloads', '--python', $manifest.python_version)
if ($LaunchArgs -contains '--offline') { $uvArgs += '--offline' }
& $uv @uvArgs (Join-Path $PSScriptRoot 'portable.py') @LaunchArgs
exit $LASTEXITCODE
