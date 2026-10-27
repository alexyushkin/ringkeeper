# Settings for a server install (Docker): makes .env from .env.example if there
# isn't one, and puts a strong random POSTGRES_PASSWORD in it if it has none.
# A password that's already set is never changed.
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1
#
# Mac and Linux: scripts/setup-env.sh does the same.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$utf8 = New-Object System.Text.UTF8Encoding($false)

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    "Made .env from .env.example."
}

$text = [IO.File]::ReadAllText((Resolve-Path .env), $utf8)
$found = [regex]::Matches($text, "(?m)^POSTGRES_PASSWORD=([^\r\n]*)")
if ($found.Count -and $found[$found.Count - 1].Groups[1].Value.Trim()) {
    "POSTGRES_PASSWORD is already set in .env; it stays as it is."
    exit 0
}

# A database that already exists keeps the password it was first started with
# (an install that never set one has the old default). A new one here would
# lock it out, so that's a password change instead.
$named = [regex]::Match($text, "(?m)^COMPOSE_PROJECT_NAME=([^\r\n]*)")
$project = if ($named.Success -and $named.Groups[1].Value.Trim()) { $named.Groups[1].Value.Trim() } else { (Split-Path -Leaf (Get-Location)).ToLower() }
if (Get-Command docker -ErrorAction SilentlyContinue) {
    $volume = docker volume ls -q --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.volume=pgdata" 2>$null
    if ($volume) {
        Write-Error ("This install's database already exists, started without POSTGRES_PASSWORD, " +
            "so its password is the old default. Set a new one as docs/INSTALL.md says in " +
            "`"Change the database password`".")
    }
}

$bytes = New-Object byte[] 24
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$password = -join ($bytes | ForEach-Object { $_.ToString("x2") })
if ($found.Count) {
    $text = [regex]::Replace($text, "(?m)^POSTGRES_PASSWORD=[^\r\n]*", "POSTGRES_PASSWORD=$password")
} else {
    $text = $text.TrimEnd("`r", "`n") + "`nPOSTGRES_PASSWORD=$password`n"
}
[IO.File]::WriteAllText((Join-Path (Get-Location) .env), $text, $utf8)
"Put a new random database password in .env (POSTGRES_PASSWORD)."
