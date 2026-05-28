#!/bin/bash
# 在服务器上跑这个脚本，激活 dota-stats systemd 服务。

set -e

UNIT=/etc/systemd/system/dota-stats.service

echo "[1/6] 清掉 Windows 换行符（CRLF -> LF）..."
sed -i 's/\r$//' "$UNIT"

echo "[2/6] systemd 语法校验..."
systemd-analyze verify "$UNIT"

echo "[3/6] 关掉手动跑的 gunicorn..."
pkill -f 'gunicorn.*web.app:app' || echo "  (没在跑，跳过)"
sleep 2

echo "[4/6] systemd reload + enable + start..."
systemctl daemon-reload
systemctl enable --now dota-stats
sleep 2

echo ""
echo "=== status ==="
systemctl is-active dota-stats
echo ""
echo "=== port 80 ==="
ss -tlnp | grep ':80 ' || echo "  (没监听 80)"
echo ""
echo "=== last 10 log lines ==="
journalctl -u dota-stats -n 10 --no-pager
