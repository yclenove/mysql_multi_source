param(
  [int]$Count = 10
)

$pluginApi = "http://127.0.0.1:8888/plugin?action=a&name=mysql_multi_source"
Write-Host "[INFO] 开始压测，任务数: $Count"
Write-Host "[INFO] 请确保在宝塔内已创建 source 与 db_mappings"

for ($i = 1; $i -le $Count; $i++) {
  $sourceId = "source_$i"
  $taskJson = "{`"source_id`":`"$sourceId`",`"mode`":`"logical`"}"
  Write-Host "[INFO] create task #$i for $sourceId"
  Write-Host "curl -s -X POST `"$pluginApi&s=create_bootstrap_task`" -d '$taskJson'"
}

Write-Host "[INFO] 压测建议:"
Write-Host "1) 并发 5/10/20 三档执行"
Write-Host "2) 记录任务成功率、平均时长、失败类型"
Write-Host "3) 压测期间监控 CPU/内存/磁盘IO 与 MySQL 延迟"
