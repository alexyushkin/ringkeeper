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

# Git Credential Manager (a .NET app, about a quarter of MinGit): workers sign
# in with tokens, never interactively, so it goes, and git stops asking for it.
$Bin = Join-Path $Target "ucrt64\bin"
$Gcm = @(
    "git-credential-manager.exe", "git-credential-manager.exe.config", "git-credential-helper-selector.exe",
    "gcmcore.dll", "msalruntime.dll", "av_libglesv2.dll", "libHarfBuzzSharp.dll", "libSkiaSharp.dll"
)
foreach ($name in $Gcm) { Remove-Item (Join-Path $Bin $name) -ErrorAction SilentlyContinue }
# Its .NET assemblies (Avalonia, MSAL, System.*, ...): git itself has none.
foreach ($dll in Get-ChildItem $Bin -Filter *.dll) {
    try { [void][Reflection.AssemblyName]::GetAssemblyName($dll.FullName); Remove-Item $dll.FullName }
    catch [BadImageFormatException] { }
}
Remove-Item -Recurse -Force (Join-Path $Target "ucrt64\doc\git-credential-manager") -ErrorAction SilentlyContinue
Remove-Item (Join-Path $Target "ucrt64\share\doc\git-doc\git-credential-manager.html") -ErrorAction SilentlyContinue
$Config = Join-Path $Target "etc\gitconfig"
$text = (Get-Content $Config -Raw) -replace "\[credential\]\r?\n\s*helper = manager\r?\n", ""
[IO.File]::WriteAllText($Config, $text)
if ((Get-Content $Config -Raw) -match "helper = manager") { throw "MinGit's gitconfig still names the credential manager" }
& (Join-Path $Target "cmd\git.exe") --version
if ($LASTEXITCODE -ne 0) { throw "git doesn't run after removing the credential manager" }
Write-Output "MinGit $Version in $((Resolve-Path $Target).Path)"
