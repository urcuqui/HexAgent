#!/usr/bin/env bash
# HexAgent lab launcher for a VM/host with Docker available.
#
# It creates .env from .env.example when needed, builds the Docker image, and
# starts the Flask web UI through Docker Compose. The service is exposed on
# localhost:8000 by default; override with HEXAGENT_WEB_PORT=8080.
#
# Pass --install to register HexAgent as a systemd service that starts on boot:
#   sudo ./run_lab.sh --install
set -euo pipefail

LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_PORT="${HEXAGENT_WEB_PORT:-8000}"
OS_ID="unknown"

# ------------------------------------------------------------------ #
# --install: create and enable a systemd unit, then exit.            #
# ------------------------------------------------------------------ #
if [[ "${1:-}" == "--install" ]]; then
    # Detect which compose command is available and build the full exec path
    if /usr/bin/docker compose version >/dev/null 2>&1; then
        COMPOSE_EXEC="/usr/bin/docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        COMPOSE_EXEC="$(command -v docker-compose)"
    else
        echo "[!] Neither 'docker compose' nor 'docker-compose' found. Install one first."
        exit 1
    fi

    SERVICE_FILE=/etc/systemd/system/hexagent-lab.service
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=HexAgent Lab (Docker Compose)
Documentation=https://github.com/curcuqui/HexAgent
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${LAB_DIR}
Environment=DOCKER_BUILDKIT=1
ExecStart=${COMPOSE_EXEC} up -d
ExecStop=${COMPOSE_EXEC} stop
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now hexagent-lab
    echo "[*] hexagent-lab service installed and started."
    echo "[*] HexAgent arrancará automáticamente en cada reinicio."
    echo "[*] Comandos útiles:"
    echo "    systemctl status hexagent-lab"
    echo "    systemctl stop   hexagent-lab"
    echo "    journalctl -u hexagent-lab -f"
    exit 0
fi

export DOCKER_BUILDKIT=1
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

echo "[*] Building and starting HexAgent containers (app + proxy)..."
"${COMPOSE[@]}" up --build -d

echo "[*] Waiting for HexAgent to become reachable on port 80..."
HEALTH_OK=0
for _ in $(seq 1 30); do
    if curl -fsS "http://localhost/health" >/dev/null 2>&1; then
        HEALTH_OK=1
        break
    fi
    sleep 2
done

if [ "$HEALTH_OK" != "1" ]; then
    echo "[!] HexAgent did not become healthy at http://localhost/health"
    echo "[!] Inspect logs with: ${COMPOSE[*]} logs"
    exit 1
fi

VM_IP="$(hostname -I | awk '{print $1}')"
echo
echo "[*] HexAgent web UI:  http://${VM_IP}"
echo "[*] Health endpoint:  http://${VM_IP}/health"
echo
"${COMPOSE[@]}" ps

echo
echo "[*] Useful commands:"
echo "    ${COMPOSE[*]} logs -f             # tail logs"
echo "    ${COMPOSE[*]} exec app pytest -q  # run tests"
echo "    ${COMPOSE[*]} down                # stop the lab"
