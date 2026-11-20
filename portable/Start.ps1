$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot 'runtime\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Bundled Python runtime is missing.' }
$env:ANTIDUPEIMAGE_HOME = Join-Path $PSScriptRoot 'data'
$env:ANTIDUPEIMAGE_STATIC_DIR = Join-Path $PSScriptRoot 'frontend'
$env:HF_HOME = Join-Path $PSScriptRoot 'cache\huggingface'
$env:TORCH_HOME = Join-Path $PSScriptRoot 'cache\torch'
$tempRoot = Join-Path $PSScriptRoot 'cache\temp'
[IO.Directory]::CreateDirectory($tempRoot) > $null
$env:TEMP = $tempRoot
$env:TMP = $tempRoot
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()
$origin = "http://127.0.0.1:$port"
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
function New-Capability {
    $bytes = [byte[]]::new(32)
    $rng.GetBytes($bytes)
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}
$env:ANTIDUPEIMAGE_LAUNCH_NONCE = New-Capability
$env:ANTIDUPEIMAGE_UI_ORIGIN = $origin
$bootstrap = New-Capability
$rng.Dispose()
$directory = Join-Path $tempRoot ('antidupeimage-handoff.' + [Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($directory) > $null
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = [Security.AccessControl.DirectorySecurity]::new()
$acl.SetAccessRuleProtection($true, $false)
$rule = [Security.AccessControl.FileSystemAccessRule]::new(
    $identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
$acl.AddAccessRule($rule)
[IO.Directory]::SetAccessControl($directory, $acl)
$handoff = Join-Path $directory 'handoff.html'
$html = '<!doctype html><meta name="referrer" content="no-referrer">' +
    '<meta name="antidupeimage-bootstrap" content="' + $bootstrap + '">' +
    '<script>location.replace("' + $origin + '/#bootstrap=' + $bootstrap + '");</script>'
[IO.File]::WriteAllText($handoff, $html, [Text.UTF8Encoding]::new($false))
$env:ANTIDUPEIMAGE_HANDOFF_PATH = $handoff
$server = $null
try {
    $server = Start-Process -FilePath $python -ArgumentList @(
        '-I', '-B', '-m', 'uvicorn', 'app.main:app', '--app-dir', 'backend',
        '--host', '127.0.0.1', '--port', $port, '--no-access-log'
    ) -WorkingDirectory $PSScriptRoot -NoNewWindow -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    $ready = $false
    while ([DateTime]::UtcNow -lt $deadline -and -not $server.HasExited) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 `
                -Uri "$origin/_antidupeimage/launch-health" `
                -Headers @{ 'X-AntiDupeImage-Launch-Nonce' = $env:ANTIDUPEIMAGE_LAUNCH_NONCE }
            if (($response.Content | ConvertFrom-Json).nonce -ceq $env:ANTIDUPEIMAGE_LAUNCH_NONCE) {
                $ready = $true
                break
            }
        } catch { }
        Start-Sleep -Milliseconds 100
    }
    if (-not $ready) { throw 'The server did not pass its launch ownership check.' }
    Write-Host "UI: $origin"
    if ($env:ANTIDUPEIMAGE_NO_BROWSER -ne '1') { Start-Process -FilePath $handoff }
    while (-not $server.HasExited) { Start-Sleep -Milliseconds 500 }
    exit $server.ExitCode
} finally {
    if ($null -ne $server -and -not $server.HasExited) {
        & taskkill.exe /PID $server.Id /T /F *> $null
    }
    if ([IO.File]::Exists($handoff)) { [IO.File]::Delete($handoff) }
    if ([IO.Directory]::Exists($directory)) { [IO.Directory]::Delete($directory, $false) }
}
