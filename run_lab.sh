#!/usr/bin/env bash
# HexAgent lab launcher for a VM/host with Docker available.
#
# It creates .env from .env.example when needed, builds the Docker image, and
# starts the Flask web UI through Docker Compose. The service is exposed on
# localhost:8000 by default; override with HEXAGENT_WEB_PORT=8080.
set -euo pipefail

LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_PORT="${HEXAGENT_WEB_PORT:-8000}"
OS_ID="unknown"

export HEXAGENT_WEB_PORT="$WEB_PORT"
export HEXAGENT_MOCK_MODE="${HEXAGENT_MOCK_MODE:-true}"
export HEXAGENT_ENABLE_NMAP="${HEXAGENT_ENABLE_NMAP:-false}"

echo "[*] Starting HexAgent lab..."

if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-unknown}"
fi

if [ ! -d "$LAB_DIR" ]; then
    echo "[!] Lab directory not found: $LAB_DIR"
    exit 1
fi

cd "$LAB_DIR"

if ! command -v docker >/dev/null 2>&1; then
    echo "[!] Docker is not installed or is not in PATH."
    if [ "$OS_ID" = "debian" ] || [ "$OS_ID" = "ubuntu" ]; then
        echo "[!] On Debian/Ubuntu, install it with:"
        echo "    sudo apt update"
        echo "    sudo apt install -y docker.io docker-compose-plugin"
        echo "    sudo systemctl enable --now docker"
    fi
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "[!] Docker is installed, but the daemon is not reachable."
    if [ "$OS_ID" = "debian" ] || [ "$OS_ID" = "ubuntu" ]; then
        echo "[!] Start Docker with:"
        echo "    sudo systemctl enable --now docker"
        echo "[!] If this is a permission issue, either run this script with sudo or add your user to the docker group:"
        echo "    sudo usermod -aG docker \$USER"
        echo "    newgrp docker"
    fi
    exit 1
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    echo "[!] Docker Compose is not installed."
    if [ "$OS_ID" = "debian" ] || [ "$OS_ID" = "ubuntu" ]; then
        echo "[!] Install the v2 plugin with:"
        echo "    sudo apt update"
        echo "    sudo apt install -y docker-compose-plugin"
        echo "[!] Or install the legacy package: sudo apt install -y docker-compose"
    else
        echo "[!] Install either 'docker-compose-plugin' or legacy 'docker-compose'."
    fi
    exit 1
fi

if [ ! -f "$LAB_DIR/.env" ]; then
    if [ -f "$LAB_DIR/.env.example" ]; then
        cp "$LAB_DIR/.env.example" "$LAB_DIR/.env"
        echo "[*] Created .env from .env.example"
    else
        echo "[!] Neither .env nor .env.example found in $LAB_DIR"
        exit 1
    fi
fi

echo "[*] Building and starting HexAgent web container..."
"${COMPOSE[@]}" up --build -d app

echo "[*] Waiting for HexAgent to become reachable..."
HEALTH_OK=0
for _ in $(seq 1 30); do
    if curl -fsS "http://localhost:${WEB_PORT}/health" >/dev/null 2>&1; then
        HEALTH_OK=1
        break
    fi
    sleep 2
done

if [ "$HEALTH_OK" != "1" ]; then
    echo "[!] HexAgent did not become healthy at http://localhost:${WEB_PORT}/health"
    echo "[!] Inspect logs with: ${COMPOSE[*]} logs app"
    exit 1
fi

echo
echo "[*] HexAgent web UI:  http://localhost:${WEB_PORT}"
echo "[*] Health endpoint:  http://localhost:${WEB_PORT}/health"
echo
"${COMPOSE[@]}" ps

echo
echo "[*] Useful commands:"
echo "    ${COMPOSE[*]} logs -f             # tail logs"
echo "    ${COMPOSE[*]} exec app pytest -q  # run tests"
echo "    ${COMPOSE[*]} down                # stop the lab"
