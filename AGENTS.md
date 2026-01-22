# Repository Guidelines

## Project Structure & Module Organization
- Root contains runtime binaries and configs for the Hyperliquid non-validator node.
- Binaries: `hl-visor`, `hl-node`, `fifo_listener`, and `book_server` (symlink).
- Config/ops files: `systemd/` units, `install_systemd_units.sh`, `init_deploy.sh`, `visor.json`, `override_gossip_config.json`, `book_tmpfs_init.sh`.
- Runtime data (mounted/tmpfs): `hl/`, `hl_book/`, `hl_tmp/` (ignored by git).

## Build, Test, and Development Commands
- `./book_tmpfs_init.sh` initializes FIFOs and hourly links.
- `python_example.py` runs the scheduled maintenance tasks via `AsyncIOScheduler`, including hourly rotation + cleanup; it is started by `fifo_listener.service`.
- Install systemd units: `./install_systemd_units.sh`.
- Install and run the service (user scope): `systemctl --user start hyperliquid.service` and `systemctl --user status hyperliquid.service`.
- Manual run (mirrors the unit file):
  `./hl-visor run-non-validator --serve-info --write-fills --write-order-statuses --write-raw-book-diffs --disable-output-file-buffering --batch-by-block --replica-cmds-style recent-actions`.

## Coding Style & Naming Conventions
- Bash scripts use `set -eu`, uppercase path variables, and 4-space indentation; keep shell logic explicit and minimal.
- JSON configs use 2-space indentation and snake_case keys (e.g., `bind_ip`, `root_node_ips`).
- File naming favors functional suffixes like `*_config.json`, `*_by_block`, and systemd `*.service` units.

## Testing Guidelines
- No automated test suite is present. Validate changes by running the relevant script or service and checking logs in `hl_book/fifo.log` or `hl_tmp/log/`.

## Commit & Pull Request Guidelines
- Git history uses short, lowercase messages (e.g., "add conf", "update"). Keep commits concise and imperative.
- PRs should describe runtime impact, list config changes, and call out required restarts or cron updates.

## Operations & Safety Notes
- `hl_book/` and `hl_tmp/` are tmpfs mounts (see `fstab`); data is ephemeral and cleaned by `crontab` jobs.
- Avoid committing generated data or logs; `.gitignore` already excludes runtime directories and binaries.
