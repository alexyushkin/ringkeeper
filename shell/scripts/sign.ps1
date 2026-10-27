# Signs files for the Windows build. Tauri runs it for the app, the installer's
# plugins, the uninstaller and the installer (bundle.windows.signCommand), and
# the release workflow runs it on the bundled Python's unsigned programs and
# libraries. Only used when signing secrets are set (docs/SIGNING.md).
#
#   RK_SIGN_MODE=azure     Azure Artifact Signing: RK_SIGN_DLIB (the signing
#                          client's dlib) and RK_SIGN_METADATA (endpoint, account,
#                          profile); the identity comes from AZURE_TENANT_ID,
#                          AZURE_CLIENT_ID and AZURE_CLIENT_SECRET.
#   RK_SIGN_MODE=signtool  Any certificate signtool can use (an OV certificate in
#                          a CA's cloud key service, or on a token): RK_SIGN_ARGS
#                          holds signtool's certificate arguments, RK_SIGN_TIMESTAMP
#                          the CA's timestamp server.
#
#   sign.ps1 FILE [FILE...]

$ErrorActionPreference = "Stop"
if ($args.Count -eq 0) { throw "usage: sign.ps1 FILE [FILE...]" }

$SignTool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $SignTool) { throw "signtool.exe not found (Windows SDK)" }

switch ($env:RK_SIGN_MODE) {
    "azure" {
        $how = @("/tr", "http://timestamp.acs.microsoft.com", "/dlib", $env:RK_SIGN_DLIB, "/dmdf", $env:RK_SIGN_METADATA)
    }
    "signtool" {
        $timestamp = if ($env:RK_SIGN_TIMESTAMP) { $env:RK_SIGN_TIMESTAMP } else { "http://timestamp.digicert.com" }
        # RK_SIGN_ARGS is one line of signtool arguments, split like a command line.
        $how = @("/tr", $timestamp) + [System.Management.Automation.PSParser]::Tokenize($env:RK_SIGN_ARGS, [ref]$null).Content
    }
    default { throw "RK_SIGN_MODE must be azure or signtool" }
}
& $SignTool.FullName sign /fd SHA256 /td SHA256 @how @args
if ($LASTEXITCODE -ne 0) { throw "signing failed ($LASTEXITCODE): $args" }
