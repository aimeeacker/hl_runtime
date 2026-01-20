#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="${RUN_USER:-${SUDO_USER:-$USER}}"
CHAIN="${CHAIN:-}"
SERVICE_NAME="hyperliquid.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
SYSCTL_PATH="/etc/sysctl.d/99-hl.conf"
FSTAB_PATH="/etc/fstab"
BEGIN_MARKER="# BEGIN hl_runtime"
END_MARKER="# END hl_runtime"

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

run_as_user() {
    if [ "$RUN_USER" = "$USER" ]; then
        "$@"
    else
        sudo -u "$RUN_USER" -H "$@"
    fi
}

detect_chain() {
    local parsed=""
    if [ -z "$CHAIN" ] && [ -f "$ROOT/visor.json" ]; then
        parsed=$(sed -n 's/.*"chain"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$ROOT/visor.json" | head -n 1)
    fi
    CHAIN="${CHAIN:-${parsed:-Mainnet}}"
    case "$CHAIN" in
        Mainnet|Testnet) ;;
        *)
            echo "CHAIN must be Mainnet or Testnet (got: $CHAIN)"
            exit 1
            ;;
    esac
}

ensure_visor_json() {
    if [ ! -f "$ROOT/visor.json" ]; then
        echo "{\"chain\": \"$CHAIN\"}" > "$ROOT/visor.json"
    fi
}

update_hl_visor() {
    local base_url=""
    if [ "$CHAIN" = "Testnet" ]; then
        base_url="https://binaries.hyperliquid-testnet.xyz/Testnet"
    else
        base_url="https://binaries.hyperliquid.xyz/Mainnet"
    fi

    curl -fsSL "$base_url/hl-visor" -o "$ROOT/hl-visor"
    chmod a+x "$ROOT/hl-visor"
    curl -fsSL "$base_url/hl-visor.asc" -o "$ROOT/hl-visor.asc"

    run_as_user gpg --import "$ROOT/pub_key.asc"
    run_as_user gpg --verify "$ROOT/hl-visor.asc" "$ROOT/hl-visor"
}

write_service() {
    cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=Hyperliquid Non-Validator Node (hl-visor)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
UMask=0022
MemoryHigh=49G
MemoryMax=50G
MemorySwapMax=0

WorkingDirectory=$ROOT
Environment=RUST_LOG=info

ExecStartPre=$ROOT/book_tmpfs_init.sh
ExecStart=$ROOT/hl-visor run-non-validator --serve-info --write-fills --write-order-statuses --write-raw-book-diffs --disable-output-file-buffering --batch-by-block --replica-cmds-style recent-actions
ExecStartPost=/bin/bash -c '(trap - SIGINT; exec $ROOT/fifo_listener >> $ROOT/hl_book/fifo.log 2>&1) &'

KillSignal=SIGINT
TimeoutStopSec=120
Restart=no

LimitNOFILE=1048576
LimitNPROC=1048576
Nice=-5
SyslogIdentifier=hyex

NoNewPrivileges=true
PrivateTmp=true
ProtectHome=false
ProtectSystem=full
ReadWritePaths=$ROOT

[Install]
WantedBy=multi-user.target
EOF
    chmod 0644 "$SERVICE_PATH"
    systemctl daemon-reload
}

write_sysctl() {
    cat > "$SYSCTL_PATH" <<EOF
fs.pipe-max-size = 16777216
net.core.wmem_max = 16777216
net.core.rmem_max = 16777216
EOF
    chmod 0644 "$SYSCTL_PATH"
    sysctl -p "$SYSCTL_PATH"
}

write_fstab() {
    local run_uid
    local run_gid
    local shm_line
    local block
    local tmp

    run_uid=$(id -u "$RUN_USER")
    run_gid=$(id -g "$RUN_USER")

    shm_line="tmpfs  /dev/shm  tmpfs  size=1G,nosuid,nodev,noexec  0  0"
    #if grep -qE '^[[:space:]]*[^#].*[[:space:]]/dev/shm[[:space:]]+tmpfs' "$FSTAB_PATH"; then
    #    shm_line="# $shm_line"
    #fi

    block=$(cat <<EOF
$BEGIN_MARKER
$shm_line

# hyperliquid runtime
tmpfs  $ROOT/hl_book  tmpfs  size=511M,nosuid,nodev,noexec,uid=$run_uid,gid=$run_gid,mode=0770  0  0
# /dev/vdb1 $ROOT/hl xfs  noatime,nodiratime,logbufs=8,logbsize=256k,allocsize=512m,inode64,attr2  0  0
tmpfs  $ROOT/hl_tmp  tmpfs  size=255M,nosuid,nodev,noexec,uid=$run_uid,gid=$run_gid,mode=0770  0  0
$END_MARKER
EOF
)

    tmp=$(mktemp)
    awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
        $0 == begin {skip=1; next}
        $0 == end {skip=0; next}
        !skip {print}
    ' "$FSTAB_PATH" > "$tmp"
    printf "\n%s\n" "$block" >> "$tmp"
    install -m 0644 "$tmp" "$FSTAB_PATH"
    rm -f "$tmp"
}

write_crontab() {
    local tmp
    local block

    block=$(cat <<EOF
$BEGIN_MARKER
0 */4 * * * /usr/bin/find $ROOT/hl_book \\( -type f -o -type l \\) -mmin +2 -delete
59 * * * * $ROOT/book_tmpfs_init.sh next
#*/5 * * * * /usr/bin/find $ROOT/hl_tmp/replica_cmds -type f -mmin +3 -delete
*/5 * * * * /usr/bin/find $ROOT/hl/periodic_abci_states -type f -mmin +3 -delete
*/5 * * * * cd $ROOT/hl/hyperliquid_data/evm_db_hub_slow/checkpoint && ls -d */ | sed 's:/$::' | sort -nr | tail -n +3 | xargs -r rm -rf
$END_MARKER
EOF
)

    tmp=$(mktemp)
    crontab -u "$RUN_USER" -l 2>/dev/null | awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
        $0 == begin {skip=1; next}
        $0 == end {skip=0; next}
        !skip {print}
    ' > "$tmp"
    printf "%s\n" "$block" >> "$tmp"
    crontab -u "$RUN_USER" "$tmp"
    rm -f "$tmp"
}

main() {
    require_root
    require_cmd curl
    require_cmd gpg
    require_cmd systemctl
    require_cmd crontab
    require_cmd awk
    require_cmd sed
    require_cmd mktemp

    detect_chain
    ensure_visor_json

    mkdir -p "$ROOT/hl_book" "$ROOT/hl_tmp" "$ROOT/hl"

    update_hl_visor
    write_service
    write_fstab
    write_sysctl
    write_crontab

    echo "Done."
    echo "Service installed at $SERVICE_PATH (not enabled or started)."
    echo "To start: systemctl start $SERVICE_NAME"
    echo "To enable at boot: systemctl enable $SERVICE_NAME"
}

main "$@"
