#!/bin/bash
set -euo pipefail

# 用法:
# bash scripts/stress/stress_bootstrap_tasks.sh 20
# 参数表示连续触发的任务数量（默认10）

COUNT="${1:-10}"
PLUGIN_API="http://127.0.0.1:8888/plugin?action=a&name=mysql_multi_source"

echo "[INFO] 开始压测，任务数: ${COUNT}"
echo "[INFO] 请确保在宝塔内已创建 source 与 db_mappings"

for i in $(seq 1 "${COUNT}"); do
  SOURCE_ID="source_${i}"
  TASK_JSON='{"source_id":"'"${SOURCE_ID}"'","mode":"logical"}'
  echo "[INFO] create task #${i} for ${SOURCE_ID}"
  # 仅提供模板，实际请改为带鉴权的请求方式
  echo "curl -s -X POST \"${PLUGIN_API}&s=create_bootstrap_task\" -d '${TASK_JSON}'"
done

cat <<'EOF'
[INFO] 压测建议:
1) 并发 5/10/20 三档执行
2) 记录任务成功率、平均时长、失败类型
3) 压测期间监控 CPU/内存/磁盘IO 与 MySQL 延迟
EOF
