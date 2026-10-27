#!/bin/sh
# Settings for a server install (Docker): makes .env from .env.example if there
# isn't one, and puts a strong random POSTGRES_PASSWORD in it if it has none.
# A password that's already set is never changed.
#
#   sh scripts/setup-env.sh
#
# Windows: scripts/setup-env.ps1 does the same.
set -eu
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Made .env from .env.example."
fi

current=$(sed -n 's/^POSTGRES_PASSWORD=//p' .env | tail -n 1 | tr -d '\r')
if [ -n "$current" ]; then
    echo "POSTGRES_PASSWORD is already set in .env; it stays as it is."
    exit 0
fi

# A database that already exists keeps the password it was first started with
# (an install that never set one has the old default). A new one here would
# lock it out, so that's a password change instead.
project=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' .env | tail -n 1 | tr -d '\r')
[ -n "$project" ] || project=$(basename "$PWD" | tr '[:upper:]' '[:lower:]')
if command -v docker >/dev/null 2>&1 && [ -n "$(docker volume ls -q \
    --filter "label=com.docker.compose.project=$project" \
    --filter label=com.docker.compose.volume=pgdata 2>/dev/null)" ]; then
    echo "This install's database already exists, started without POSTGRES_PASSWORD," >&2
    echo "so its password is the old default. Set a new one as docs/INSTALL.md says" >&2
    echo "in \"Change the database password\"." >&2
    exit 1
fi

password=$(openssl rand -hex 24 2>/dev/null || od -An -N24 -tx1 /dev/urandom | tr -d ' \n')
if grep -q '^POSTGRES_PASSWORD=' .env; then
    sed "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$password/" .env > .env.new
    mv .env.new .env
else
    printf '\nPOSTGRES_PASSWORD=%s\n' "$password" >> .env
fi
echo "Put a new random database password in .env (POSTGRES_PASSWORD)."
