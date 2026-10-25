# Puts MinGit (git for Windows without bash) in shell/resources/mingit, where
# the installed app looks for it and hands it to workers
# (RINGKEEPER_WORKER_TOOLS_PATH). Pinned and checked: the same build CI tests with.
#
#   powershell -File shell/scripts/fetch-mingit.ps1

$ErrorActionPreference = "Stop"
$Version = "2.56.0.windows.2"
$Url = "https://github.com/git-for-windows/git/releases/download/v$Version/MinGit-2.56.0.2-64-bit.zip"
$Sha256 = "da35e72aa21c005a5a0d298cfbae110bc1609a815730ea0dde84b01a1b3cd3be"

$Target = Join-Path $PSScriptRoot "..\resources\mingit"
$Zip = Join-Path ([System.IO.Path]::GetTempPath()) "MinGit-$Version.zip"

Invoke-WebRequest $Url -OutFile $Zip -UseBasicParsing
if ((Get-FileHash $Zip -Algorithm SHA256).Hash -ne $Sha256) {
    Remove-Item $Zip
    throw "MinGit checksum mismatch"
}
if (Test-Path $Target) { Remove-Item -Recurse -Force $Target }
Expand-Archive $Zip -DestinationPath $Target
Remove-Item $Zip
if (Get-ChildItem $Target -Recurse -Filter bash.exe) { throw "this MinGit has a bash" }
Write-Output "MinGit $Version in $((Resolve-Path $Target).Path)"
