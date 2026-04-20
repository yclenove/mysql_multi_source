# mysql_multi_source

`mysql_multi_source` 是面向宝塔面板的 MySQL 多源复制插件。

## 项目目标

- 支持单从库实例接入多个主库（多 channel）
- 提供可观测、可诊断、可恢复的复制管理能力
- 在大库场景下提供更高效的初始化同步方案

## 当前进度（阶段一骨架）

- 已完成插件基础文件：
  - `info.json`
  - `install.sh`
  - `index.html`
  - `mysql_multi_source_main.py`
  - `start_sync.py`
- 已完成基础 API 骨架：
  - `health_check`
  - `list_sources`
  - `add_source`
  - `remove_source`
  - `start_channel`（当前为骨架状态切换）
  - `stop_channel`（当前为骨架状态切换）
  - `channel_status`
- 已建立项目规则体系：`.cursor/rules/*.mdc`

## 文档索引

- 设计文档：`docs/设计文档.md`
- 开发文档：`docs/开发文档.md`
- 开发留痕：`docs/开发留痕.md`

## 下一阶段计划

1. 设计并实现大库初始化流程（物理方式优先，逻辑方式兜底）
2. 增加复制映射配置（`source_db -> target_db`）与流程校验
3. 增强状态采集（延迟、错误分类、诊断建议）

## 阶段二进展（已完成）

- `start_channel` / `stop_channel` 已接入真实 SQL（按 channel）
- 新增 `test_source_connection` 主库连通性检查
- 新增 `get_gtid_status` GTID 状态检查
- `channel_status` 支持从 MySQL 实时拉取并刷新状态
- 前端新增：GTID 检查、来源连通性检查、状态查看

## 阶段三至阶段五进展（已完成）

- 新增库映射能力：
  - `set_db_mappings`
  - `list_db_mappings`
- 新增初始化任务编排能力：
  - `create_bootstrap_task`
  - `run_bootstrap_task`
  - `get_bootstrap_tasks`
  - `cancel_bootstrap_task`
- 新增可观测与诊断能力：
  - `diagnose_source`
  - `get_source_logs`
  - `overview_metrics`
  - `source_detail`
- 安全增强：
  - 来源列表中的复制密码脱敏显示
  - 关键操作写入按来源日志

## 阶段六进展（已完成）

- 初始化任务支持后台异步触发：
  - `trigger_bootstrap_task`
  - `get_bootstrap_task`
- `start_sync.py` 支持按 `task_id` 调用任务方法
- `run_bootstrap_task` 支持按步骤推进进度与状态更新（异步执行链路）
- 前端新增：
  - 输入任务 ID 后可执行“后台执行/取消任务/任务详情”
