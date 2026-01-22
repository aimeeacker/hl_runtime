# hl_runtime

This repository contains runtime binaries, configs, and operational scripts for a Hyperliquid non-validator node. It is intended to be deployed via systemd with tmpfs-backed data directories and scheduled maintenance jobs.

## Service
- Systemd user units live in `systemd/`. Install them with `./install_systemd_units.sh` (no sudo).
- Start/stop with user systemd: `systemctl --user start hyperliquid.service` and `systemctl --user status hyperliquid.service`.
`fifo_listener.service` runs `python_example.py` to keep the scheduler alive, and `hyperliquid.service` waits for it to be active before starting.
- Manual run (matches the unit file):
  ```bash
  ./hl-visor run-non-validator --serve-info --write-fills --write-order-statuses --write-raw-book-diffs --disable-output-file-buffering --batch-by-block --replica-cmds-style recent-actions
  ```

## Filesystem mounts (fstab)
Use the following entries in `/etc/fstab` (adjust device names as needed):
```fstab
tmpfs  /dev/shm  tmpfs  size=1G,nosuid,nodev,noexec  0  0

# hyperliquid runtime
tmpfs  /home/aimee/hl_runtime/hl_book  tmpfs  size=511M,nosuid,nodev,noexec,uid=1000,gid=1000,mode=0770  0  0
/dev/vdb1 /home/aimee/hl_runtime/hl xfs  noatime,nodiratime,logbufs=8,logbsize=256k,allocsize=512m,inode64,attr2  0  0
tmpfs  /home/aimee/hl_runtime/hl_tmp  tmpfs  size=255M,nosuid,nodev,noexec,uid=1000,gid=1000,mode=0770  0  0
```

## Scheduled maintenance (python)
Use `python_example.py` with `AsyncIOScheduler` to run the periodic tasks (started via `fifo_listener.service`):
- Rotate hourly links and clean `hl_book` together (minute 59 each hour).
- 5-minute maintenance cleanup (same as the previous systemd timer).
The scheduler is intended to run as `python_example.service` under user systemd.

## Kernel and network tuning (sysctl)
Add a sysctl drop-in (e.g., `/etc/sysctl.d/99-hl.conf`) and apply with `sudo sysctl -p /etc/sysctl.d/99-hl.conf`:
```conf
fs.pipe-max-size = 16777216
net.core.wmem_max = 16777216
net.core.rmem_max = 16777216
```

## Data paths
- Runtime data: `hl/`, `hl_book/`, `hl_tmp/`
- FIFO and hourly links are initialized by `book_tmpfs_init.sh`
