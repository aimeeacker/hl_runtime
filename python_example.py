# Example usage:
# 1) Build the module:
#    cargo build -p fifo_listener --release
# 2) Make it importable (pick one):
#    - export PYTHONPATH=target/release
#    - OR: ln -sf ~/order_book_server/target/release/libfifo_listener.so ~/hl_runtime/fifo_listener.so
# 3) Run:
#    python fifo_listener/python_example.py
#echo "/home/aimee/trading_packages" \
#  > ~/hl_runtime/lib/python3.12/site-packages/custom.pth


import asyncio
import logging
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

import fifo_listener
from binance.ws.reconnecting_websocket import Hyperliqueid_Websocket
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from mylogger import setup_logger
setup_logger(level=logging.INFO, muted_patterns=["apscheduler.*"])


logger = logging.getLogger("RUNTIME")

ROOT = Path(__file__).resolve().parent
scheduler = AsyncIOScheduler(job_defaults={"coalesce": True, "max_instances": 1})
local_height = None
block_height = None


async def run_command(name: str, command: str) -> None:
    #logger.info("job=%s cmd=%s", name, command)
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=os.environ.copy(),
    )
    stdout, stderr = await proc.communicate()
    if stdout:
        #logger.info("job=%s stdout=%s", name, stdout.decode().strip())
        pass
    if stderr:
        logger.warning("job=%s stderr=%s", name, stderr.decode().strip())
    if proc.returncode != 0:
        logger.error("job=%s exited rc=%s", name, proc.returncode)


async def rotate_to_next_hour() -> None:
    FIFO_MAP = {"node_fills": "fills", "node_order_statuses": "order", "node_raw_book_diffs": "diffs"}
    now, delta = datetime.now(timezone.utc), timedelta(hours=1)
    for name in ["node_fills", "node_order_statuses", "node_raw_book_diffs"]:
        base = ROOT / "hl_book" / f"{name}_by_block" / "hourly"
        src = base / now.strftime("%Y%m%d")
        cur = src / str(now.hour)
        nxt = src / str((now + delta).hour)
        if nxt.exists():
            continue  # already rotated
        if cur.exists():
            os.rename(cur, nxt)
        else:
            target = ROOT / "hl_book" / "node_fifo" / FIFO_MAP[name]
            os.symlink(target, nxt)

        if now.hour == 23:
            dst_dir = base / (now + delta).strftime("%Y%m%d")
            os.rename(src, dst_dir)

    logger.info(f"rotate_to {(now + delta).strftime('%Y%m%d')}/{str((now + delta).hour)}")

async def timer_maintenance_5min() -> None:
    cmd1 = f"/usr/bin/find {ROOT}/hl/periodic_abci_states -type f -mmin +3 -delete"
    cmd2 = (
        f"cd {ROOT}/hl/hyperliquid_data/evm_db_hub_slow/checkpoint && "
        "ls -d */ | sed 's:/$::' | sort -nr | tail -n +3 | xargs -r rm -rf"
    )
    await asyncio.gather(
        run_command("timer_maintenance_5min_cmd1", cmd1),
        run_command("timer_maintenance_5min_cmd2", cmd2)
    )


async def get_hyperliquid_memory():
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "--user", "show", "-p", "MemoryCurrent", "--value", "hyperliquid.service",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=os.environ.copy(),
    )
    stdout, _ = await proc.communicate()
    val = stdout.decode().strip()

    if val.isdigit():
        bytes_used = int(val)
        if bytes_used < 1.8e19:
            memory_used = bytes_used / 1048576  # MiB
            #logger.info("hyperliquid.service memory_current=%.2f MiB", memory_used)
            return memory_used
    return None

async def monitor_service_health() -> None:
    # 1. Memory Check (Priority: Critical)
    mem = await get_hyperliquid_memory()
    if mem and mem > 28000:  # ~27.3 GiB
        logger.warning(f"🔄 OOM Risk: {mem:.2f} MiB. Restarting...")
        await run_command("oom_restart", "systemctl --user restart hyperliquid.service")
        return

    # 2. Synchronization Check
    if local_height is None:
        logger.info("ℹ️ Init start hyperliquid.service")
        await run_command("init_start", "systemctl --user start hyperliquid.service")
        return

    # If local_height is missing (script startup), treat as 0 (huge lag)
    # The cron job runs every 1 min, giving enough warm-up time for FIFO.
    lh = local_height if local_height is not None else 0
    diff = block_height - lh

    if diff > 1500:
        logger.warning(f"🔄 Sync Lag: {diff} blocks (H:{block_height} L:{lh}). Restarting...")
        p = ROOT / "hl/hyperliquid_data/abci_state.rmp"
        p.unlink(missing_ok=True)
        await run_command("lag_restart", "systemctl --user restart hyperliquid.service")


def init_environment() -> None:
    book = ROOT / "hl_book"
    temp = ROOT / "hl_tmp"
    pipe_dir = book / "node_fifo"
    hl_dir = ROOT / "hl"
    suffix = "_by_block"
    
    # 1. Base directories (and FIFOs)
    temp.mkdir(parents=True, exist_ok=True)
    pipe_dir.mkdir(parents=True, exist_ok=True)
    (hl_dir / "periodic_abci_states").mkdir(parents=True, exist_ok=True)
    
    for pipe in ["fills", "order", "diffs"]:
        p = pipe_dir / pipe
        if not p.exists():
            os.mkfifo(p)

    # 2. Symlinks
    def force_symlink(target, link):
        link = Path(link)
        if link.is_symlink() or link.exists():
            link.unlink()
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)

    force_symlink(hl_dir, "/home/aimee/hl")
    force_symlink(temp, hl_dir / "data")

    for name in ["node_fills", "node_order_statuses", "node_raw_book_diffs"]:
        base = f"{name}{suffix}"
        force_symlink(book / base, temp / base)

    # 3. Current hour link
    now = datetime.now(timezone.utc)
    d, h = now.strftime("%Y%m%d"), str(now.hour)
    FIFO_MAP = {"node_fills": "fills", "node_order_statuses": "order", "node_raw_book_diffs": "diffs"}

    for name, pipe in FIFO_MAP.items():
        base = f"{name}{suffix}"
        hour_dir = book / base / "hourly" / d
        hour_dir.mkdir(parents=True, exist_ok=True)
        force_symlink(pipe_dir / pipe, hour_dir / h)
    
    logger.info(f"✅ init_environment done: {d} hour {h}")


def on_height(height: int) -> None:
    global local_height
    local_height = height
    #logger.info("Local Height: %d, Hyperliquid Height: %d", local_height, block_height)

async def on_hyex_message(message: dict) -> None:
    global block_height
    block_height = message[0]["height"]
    if block_height % 10000 == 100:
        #logger.info("Hyperliquid Height: %d, Local Height: %d", block_height, local_height)
        await monitor_service_health()
    #block_time = message[0]["blockTime"]

async def main():
    try:
        listener = fifo_listener.FifoListener()        
        global local_height, block_height
        hyex_ws = Hyperliqueid_Websocket(url="wss://rpc.hyperliquid.xyz")
        await hyex_ws._setup(callback=on_hyex_message, streams={"channel": "explorerBlock"}, ws_name="explorer")
        await hyex_ws._start()
        scheduler.add_job(rotate_to_next_hour, CronTrigger(minute="59", second="55"))
        scheduler.add_job(timer_maintenance_5min, CronTrigger(minute="*/5", second="15"))
        #scheduler.add_job(monitor_service_health, CronTrigger(minute="*/1", second="10"))#
        scheduler.start()


        init_environment()
        listener.start(on_height)
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("❌ 收到 Ctrl+C, 程序退出")
    finally:
        listener.stop()
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 收到 Ctrl+C, 程序退出")
    finally:
        logger.info("✅ 程序已结束")
