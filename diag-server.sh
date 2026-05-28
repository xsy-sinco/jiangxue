#!/bin/bash
# 服务器健康检查

PROJ=/var/www/html/dota-inhouse-stats

echo "=== 服务状态 ==="
systemctl is-active dota-stats
echo
echo "=== aggregate.json 文件 ==="
ls -la $PROJ/data/aggregate.json 2>&1
echo
echo "=== aggregate.json 头部 ==="
head -c 300 $PROJ/data/aggregate.json 2>&1
echo
echo
echo "=== matches 缓存数量 ==="
ls $PROJ/data/matches/*.json 2>/dev/null | wc -l
echo
echo "=== /api/stats 返回 ==="
curl -s http://localhost/api/stats | head -c 500
echo
echo
echo "=== /api/stats 是否含 players 字段（新格式）==="
curl -s http://localhost/api/stats | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    data = d.get('data')
    if not data:
        print('data is null/empty')
    else:
        print('summary keys:', list(data.get('summary', {}).keys())[:5])
        print('matches count:', len(data.get('matches', [])))
        if data.get('matches'):
            m0 = data['matches'][0]
            print('match[0] has players field:', 'players' in m0)
            print('match[0] players count:', len(m0.get('players', [])))
except Exception as e:
    print('error:', e)
"
echo
echo "=== 最近日志 ==="
journalctl -u dota-stats -n 15 --no-pager
