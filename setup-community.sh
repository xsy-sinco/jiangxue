#!/bin/bash
# 一次性：在服务器上为社区功能（Stage 1）做准备：
#   1. 建 community/ 目录 + uploads/avatars
#   2. 生成 FLASK_SECRET_KEY 并写入 systemd unit
#   3. reload + restart 服务

set -e

PROJ=/var/www/html/dota-inhouse-stats
UNIT=/etc/systemd/system/dota-stats.service

echo "[1/4] 建 community 目录..."
mkdir -p "$PROJ/community/uploads/avatars"
chmod -R 755 "$PROJ/community"
ls -la "$PROJ/community"

echo
echo "[2/4] 检查是否已有 FLASK_SECRET_KEY..."
if grep -q "FLASK_SECRET_KEY" "$UNIT"; then
    echo "  已存在，跳过"
else
    KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    # 在 [Service] 段后插入 Environment 行（用 awk 处理）
    awk -v k="$KEY" '
        /^\[Service\]/ { print; print "Environment=FLASK_SECRET_KEY=" k; next }
        { print }
    ' "$UNIT" > /tmp/unit.new && mv /tmp/unit.new "$UNIT"
    echo "  已写入 FLASK_SECRET_KEY"
fi

echo
echo "[3/4] systemd reload + restart..."
systemctl daemon-reload
systemctl restart dota-stats
sleep 2

echo
echo "[4/4] 验证..."
echo "  status: $(systemctl is-active dota-stats)"
echo "  端口:"
ss -tlnp | grep ':80 ' | head -1 || echo "  (没听 80)"
echo "  最近日志:"
journalctl -u dota-stats -n 5 --no-pager
echo "  community/:"
ls "$PROJ/community/"
