param([Parameter(ValueFromRemainingArguments = $true)][string[]]$LaunchArgs)
& "$PSScriptRoot\scripts\bootstrap.ps1" @LaunchArgs
exit $LASTEXITCODE
