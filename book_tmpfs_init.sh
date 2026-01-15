#!/bin/sh
set -eu

B="/home/aimee/hl_runtime/hl_tmp"
book="/home/aimee/hl_runtime/hl_book"
t="_by_block"

# UTC 日期：YYYYMMDD
D="$(date -u +%Y%m%d)"

# UTC 小时：用 %H 得到 00..23，再去掉前导 0（POSIX 兼容）
H="$(date -u +%H)"
H="${H#0}"
[ -n "$H" ] || H=0

# 1) 基础目录
mkdir -p "$B"

# 2) 固定 FIFO：fills order diffs
for pipe in fills order diffs; do
  if [ ! -p "$book/$pipe" ]; then
    rm -f "$book/$pipe"
    mkfifo "$book/$pipe"
  fi
done

#ln -sf "$book/node_fills${t}" "$B/node_fills${t}"
#ln -sf "$book/node_order_statuses${t}" "$B/node_order_statuses${t}"
#ln -sf "$book/node_raw_book_diffs${t}" "$B/node_raw_book_diffs${t}"

# 3) 目录结构（UTC）
mkdir -p "$book/node_fills${t}/hourly/$D"
mkdir -p "$book/node_order_statuses${t}/hourly/$D"
mkdir -p "$book/node_raw_book_diffs${t}/hourly/$D"
mkdir -p "/home/aimee/hl_runtime/hl/periodic_abci_states"

# 4) 当前小时软链接（Bootstrap）
ln -sf "$book/node_fills${t}" "$B/node_fills${t}"
ln -sf "$book/node_order_statuses${t}" "$B/node_order_statuses${t}"
ln -sf "$book/node_raw_book_diffs${t}" "$B/node_raw_book_diffs${t}"

ln -sf "$book/fills" "$book/node_fills${t}/hourly/$D/$H"
ln -sf "$book/order" "$book/node_order_statuses${t}/hourly/$D/$H"
ln -sf "$book/diffs" "$book/node_raw_book_diffs${t}/hourly/$D/$H"
ln -sf "/home/aimee/hl_runtime/hl/periodic_abci_states" "$B/periodic_abci_states"
echo "✅ [book_tmpfs_init] Infrastructure initialized for UTC $D/$H"

