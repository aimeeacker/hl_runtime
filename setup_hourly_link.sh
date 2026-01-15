#!/bin/bash
# 注意：这里不需要转义 %
B="/home/aimee/hl_runtime/hl_book"
D=$(date -u -d "+1 hour" +%Y%m%d)
H=$(date -u -d "+1 hour" +%-H)

# 记录执行日志
echo "[$(date)] Preparing for $D Hour $H" >> /tmp/my_cron.log

for type in "node_fills_by_block:fills" "node_order_statuses_by_block:order" "node_raw_book_diffs_by_block:diffs"; do 
    dir=${type%%:*}
    pipe=${type##*:}
    mkdir -p "$B/$dir/hourly/$D"
    ln -sf "$B/$pipe" "$B/$dir/hourly/$D/$H"
done

