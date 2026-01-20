#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
UNIT_DIR="$ROOT/systemd"
RUN_USER="${RUN_USER:-${SUDO_USER:-$USER}}"
ENABLE_TIMER="${ENABLE_TIMER:-1}"

require_root() {
    if [ "${EUID:-0}" -ne 0 ]; then
        echo "Please run as root: sudo $0"
        exit 1
    fi
}

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1"
        exit 1
    fi
}

install_unit() {
    local src="$1"
    local dest="/etc/systemd/system/$(basename "$src")"
    local tmp

    tmp="$(mktemp)"
    sed -e "s|@ROOT@|$ROOT|g" -e "s|@RUN_USER@|$RUN_USER|g" "$src" > "$tmp"
    install -m 0644 "$tmp" "$dest"
    rm -f "$tmp"
}

main() {
    require_root
    require_cmd install
    require_cmd mktemp
    require_cmd sed
    require_cmd systemctl

    if [ ! -d "$UNIT_DIR" ]; then
        echo "Missing systemd unit directory: $UNIT_DIR"
        exit 1
    fi

    shopt -s nullglob
    for unit in "$UNIT_DIR"/*.service "$UNIT_DIR"/*.timer "$UNIT_DIR"/*.slice; do
        install_unit "$unit"
    done

    systemctl daemon-reload

    if [ "$ENABLE_TIMER" = "1" ]; then
        systemctl enable --now hl_runtime_maintenance.timer
    fi

    echo "Installed systemd units from $UNIT_DIR"
    echo "Timer enabled: hl_runtime_maintenance.timer"
    echo "Service not started: hyperliquid.service"
}

main "$@"
