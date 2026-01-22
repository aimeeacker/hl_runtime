#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
UNIT_DIR="$ROOT/systemd"
TARGET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1"
        exit 1
    fi
}

install_unit() {
    local src="$1"
    local dest="$TARGET_DIR/$(basename "$src")"
    local tmp

    tmp="$(mktemp)"
    sed -e "s|@ROOT@|$ROOT|g" "$src" > "$tmp"
    install -m 0644 "$tmp" "$dest"
    rm -f "$tmp"
}

main() {
    if [ "${EUID:-0}" -eq 0 ]; then
        echo "Please run as your user (no sudo): $0"
        exit 1
    fi

    require_cmd install
    require_cmd mktemp
    require_cmd sed
    require_cmd systemctl

    if [ ! -d "$UNIT_DIR" ]; then
        echo "Missing systemd unit directory: $UNIT_DIR"
        exit 1
    fi

    mkdir -p "$TARGET_DIR"

    shopt -s nullglob
    for unit in "$UNIT_DIR"/*.service "$UNIT_DIR"/*.slice; do
        install_unit "$unit"
    done

    systemctl --user daemon-reload

    echo "Installed systemd units from $UNIT_DIR"
    echo "Service not started: hyperliquid.service"
    echo "Start: systemctl --user start hyperliquid.service"
    echo "Enable on login: systemctl --user enable hyperliquid.service"
}

main "$@"
