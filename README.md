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
- 用户使用手册：`docs/用户使用手册.md`
- 傻瓜式快速上手：`docs/傻瓜式快速上手.md`

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

## 阶段七至阶段十一进展（已完成）

- 双引擎初始化执行层：
  - 新增 `run_physical_bootstrap` / `run_logical_bootstrap`
  - `run_bootstrap_task` 已改为真实步骤驱动（带执行节点）
- 可靠任务系统：
  - 新增任务心跳、worker_id、checkpoint、失败重试字段
  - 新增 `recover_bootstrap_tasks` 用于卡死任务恢复
  - 增加并发接管保护，避免重复worker执行
- 诊断与可观测增强：
  - 新增 `get_task_logs`
  - `overview_metrics` 新增任务成功/失败/平均耗时指标
  - 错误分类输出（网络/权限/GTID/冲突/资源）
- UX 流程优化：
  - 任务执行与取消增加二次确认
  - 新增任务日志入口，提升问题定位效率
- hardening_plus 交付：
  - 压测脚本：`scripts/stress/stress_bootstrap_tasks.sh`、`scripts/stress/stress_bootstrap_tasks.ps1`
  - 运维文档：`docs/运维手册.md`、`docs/回滚手册.md`、`docs/上线检查清单.md`、`docs/验收报告模板.md`

## 最后一公里实装（已完成）

- 初始化任务默认模式调整为 `auto`，会自动选择 physical/logical
- 当物理工具缺失时，自动降级到 `mysqldump/mysql` 逻辑链路
- 逻辑链路已支持真实执行：
  - 按映射建目标库
  - 从主库导出 source_db
  - 导入从库 target_db

## 工具安装助手（已完成）

- 新增工具检测接口：`check_bootstrap_tools`
- 新增工具安装接口：`install_bootstrap_tool`
- 新增安装日志接口：`get_tool_install_log`
- 策略：
  - 有 root 权限时支持一键安装
  - 无 root 权限时返回手工命令指引
  - 安装命令优先系统仓库，不使用外部脚本拉取

## 主库协同完美版（已完成）

- 新增主库模式：
  - 运行角色切换 `master_mode / replica_mode`
  - 主库健康检测与报告分级
- 新增主库自动修复链路：
  - 修复预览、执行修复、重启MySQL
  - 修复前自动快照，支持快照回滚
- 新增主从双通道交互：
  - 签名配置文件导出/验证/导入
  - API握手会话（token + TTL + 一次性消费）
- 新增安全审计：
  - 审计日志查询
  - 快照列表与回滚接口
- 前端新增主库协同向导入口：
  - 主库检测、修复、重启、审计、回滚
  - 签名配置与握手导入流程

## 阶段十八进展（已完成）

- 全量审计与修复：
  - 输出 `docs/全量Review报告.md`，按高/中/低风险归类问题并给出修复项
- 前端重构（保持宝塔插件无构建链）：
  - `index.html` 新增请求适配层（错误标准化、singleFlight、请求顺序保护）
  - 新增统一结果面板与按钮执行态，减少重复提交与信息丢失
  - 来源列表渲染改为安全转义，修复关键 XSS 风险点
  - 统一文本弹窗出口，降低拼接型渲染风险
  - 初始化任务创建后自动回填 `task_id`，优化流程连续性
- 后端契约收敛：
  - `mysql_multi_source_main.py` 新增 `_ok/_fail` 返回辅助
  - 新增 `source_id` 校验与 `master_port` 范围校验
  - `add_source/set_db_mappings/create_bootstrap_task/trigger_bootstrap_task` 关键路径改为结构化错误返回

## 阶段十九进展（已完成）

- 身份引导与菜单 UX 重构：
  - 新增 `detect_running_mode`，支持自动判定建议身份（主机/从机）并返回 `confidence/evidence`
  - 前端新增身份引导区（自动推荐、可跳过、可手动切换、可重新检测）
  - 新增顶部身份状态栏与快捷切换按钮
- 菜单信息架构调整：
  - 侧栏重命名为 `快速接入 / 初始化任务 / 运行监控 / 主库运维`
  - 按身份动态显隐菜单，降低误入高风险主库操作概率
- 流程护栏增强：
  - 顶部新增 4 步主流程导航（接入 -> 映射 -> 任务 -> 监控）
  - 初始化任务相关按钮按前置条件自动禁用（来源、映射、任务ID）
  - 工具安装与压测收敛为“高级操作”折叠区

## 阶段二十进展（已完成）

- 首屏体验改为默认新手模式：
  - 首屏仅保留 `开始向导` 主动作和模式切换入口，避免按钮堆叠
  - 未开始向导前隐藏业务区，减少第一次使用认知负担
- 新手/专家双模式：
  - 新手模式按步骤展示必要操作
  - 专家模式可随时查看完整能力
- 身份与路径联动：
  - 主机/从机身份推荐继续保留，并映射到更合适的向导起点
