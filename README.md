# hl_runtime

This repository contains runtime binaries, configs, and operational scripts for a Hyperliquid non-validator node. It is intended to be deployed via systemd with tmpfs-backed data directories and scheduled maintenance jobs.

## Service
- Systemd unit: `hyperliquid.service`
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

## Scheduled maintenance (crontab)
Set these in the user crontab (or a dedicated service account):
```cron
0 */4 * * * /usr/bin/find /home/aimee/hl_runtime/hl_book \( -type f -o -type l \) -mmin +2 -delete
59 * * * * /home/aimee/hl_runtime/book_tmpfs_init.sh next
#*/5 * * * * /usr/bin/find /home/aimee/hl_runtime/hl_tmp/replica_cmds -type f -mmin +3 -delete
*/5 * * * * /usr/bin/find /home/aimee/hl_runtime/hl/periodic_abci_states -type f -mmin +3 -delete
*/5 * * * * cd /home/aimee/hl_runtime/hl/hyperliquid_data/evm_db_hub_slow/checkpoint && ls -d */ | sed 's:/$::' | sort -nr | tail -n +3 | xargs -r rm -rf
```

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
