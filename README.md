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

## 阶段二十一：v2.0 全面重构（已完成）

### 后端

- **安全修复**：
  - 复制密码全部加密存储（Fernet AES / XOR 回退），旧明文自动迁移
  - shell 命令不再嵌入密码字符串，改用 `subprocess.Popen` + `MYSQL_PWD` 环境变量
  - `_validate_privileges_text` 正则 bug 修复（`\\s` → `\s`）
  - 新增 `_with_lock` 文件锁保护 `_load_config/_save_config`
  - 所有 API 返回统一为 `_ok/_fail` 结构化格式
- **物理初始化真实落地**：
  - `_run_physical_bootstrap` 改为 SSH + xtrabackup stream 真实流水线
  - 缺少工具/SSH 不通时自动降级 logical，带完整审计日志
- **任务调度真恢复**：
  - `recover_bootstrap_tasks` 恢复后自动触发 `trigger_bootstrap_task`
  - 新增 `tick` 方法供 cron 定时巡检
- **向导编排层**（8 个新接口）：
  - `wizard_detect_env` / `wizard_preflight_source` / `wizard_list_master_dbs`
  - `wizard_recommend_bootstrap` / `wizard_start_replication`（原子）
  - `wizard_dashboard_snapshot`（消除 N+1 查询）
  - `wizard_diagnose_all` / `wizard_quick_fix`

### 前端

- **Vue3 + Vite 重写**：
  - 全新 `frontend/` 工程，使用 `vite-plugin-singlefile` 产出内联单 HTML
  - 集成 Naive UI 组件库、Pinia 状态管理
  - 宝塔安装方式零变化
- **首屏四卡片**：环境自动识别 → 推荐任务卡
- **主库向导 4 步**：体检 → 修复预览 → 一键修复 → 导出配置单
- **从库向导 5 步**：粘贴/手填 → 三维连通性 → 库 checkbox 选器 → 策略推荐 → 一键同步
- **仪表盘**：卡片化来源 + 健康灯 + 5s 轮询 + 任务进度条
- **诊断中心**：按类分组 + 一键修复 + 手工指引
- **专家视图**：保留旧 4 页签与全部原始按钮

### 文档

- 重写 `docs/傻瓜式快速上手.md`、`docs/用户使用手册.md`
- 新增 `docs/前端开发指南.md`

## 阶段二十二：安全回滚与目标库冲突预检（已完成）

### 后端

- 修复 `wizard_start_replication` 的高风险回滚缺陷：
  - 重复接入场景下若 `set_db_mappings` 失败，不再误调用 `remove_source` 删除既有来源
  - 改为恢复原有来源快照，避免误删 channel、任务和既有映射
- 新增 `check_target_db_conflicts`：
  - 检查本次提交内部是否有多个来源库映射到同一 `target_db`
  - 检查 `target_db` 是否已被其他来源占用
  - 返回结构化 `conflicts`，供前端直接渲染阻断提示

### 前端

- 从库向导 Step 3 新增“检查目标库冲突”按钮
- 当存在空目标库名或冲突时，阻止进入下一步和最终提交
- Step 5 会再次基于最新映射执行预检，避免绕过前置检查直接提交

### 价值

- 避免重复接入失败时误删已有来源，降低配置破坏风险
- 降低多源同名目标库误映射风险，减少用户误操作成本

## 阶段二十七：配置单签名校验补强（已完成）

### 后端

- 修复 `replica_verify_profile` 的签名校验缺失问题：
  - 现在会显式读取 `signature`
  - 调用 `_profile_verify(payload, signature)` 做真实验签
  - 对缺少签名、签名不匹配、payload 非法、配置单过期分别返回明确错误码
- 由于 `replica_import_profile` 与 `replica_accept_handshake` 都依赖 `replica_verify_profile`，因此配置单导入与握手接收链路一并得到修复

### 前端

- 修复从库向导第 3 步进入第 4 步时的冲突检查竞态：
  - 改为 `await runConflictCheck()`
  - 只有确认无冲突后才进入策略页并加载推荐方案

### 价值

- 防止伪造或被篡改的配置单绕过“签名配置文件”链路
- 避免目标库冲突检查尚未完成时就提前进入下一步

## 阶段二十八：握手留痕与总览查询收敛（已完成）

### 后端

- 握手链路增强：
  - `master_create_handshake` 新增 `accept_attempts`、`last_error`、`last_error_code`、`last_error_at`、`accepted_at`
  - `replica_accept_handshake` 在失败时记录错误消息、错误码、失败时间与尝试次数，并写入审计日志
- 总览性能优化：
  - `overview_metrics()` 不再逐来源调用 `_get_source_status()`
  - 改为复用 `_all_slave_status() + _map_status_row()` 一次抓取全部复制状态
- 任务触发护栏：
  - `trigger_bootstrap_task()` 对 `cancelled` 任务明确拒绝重触发，要求用户新建任务

### 前端

- 从库向导新增独立的“开始同步中”锁，避免重复点击提交
- 第 4 步进入第 5 步时，若映射仍无效或存在冲突，继续阻止进入确认页

### 价值

- 握手失败时可回溯失败原因，不再只有笼统的 `failed`
- 仪表盘总览指标与向导快照使用同一套状态抓取方式，降低 N+1 查询开销
- 减少向导与任务链路里的重复触发和误重试

## 阶段二十九：握手状态结构化与轮询防重入（已完成）

### 后端

- 握手状态接口结构化：
  - `master_create_handshake` 现在会回填 `profile_id`、`source_id`、`channel_name`
  - `handshake_status` 改为统一返回 `message/code/status/expired/accept_attempts` 等可直接展示的字段
  - 对缺少 token、token 不存在、已消费、已过期统一返回结构化错误码
- 过期状态收口：
  - 当握手已过期时，会将 session 状态收敛为 `expired`
  - 同时补上 `last_error` 与 `last_error_code`，避免前端只能看到裸字段

### 前端

- 仪表盘来源操作增加单来源执行锁，避免启动/暂停/移除被重复点击
- 仪表盘任务日志轮询增加请求序号保护，避免旧请求覆盖新窗口内容
- 从库向导任务轮询增加请求序号保护，重试流程改为先清理旧轮询再重建

### 价值

- 握手链路的状态和错误信息更稳定，专家模式与后续前端可直接消费
- 慢请求场景下减少轮询乱序覆盖与重复触发导致的状态抖动

## 阶段三十：握手全局统计与任务并发护栏（已完成）

### 后端

- 新增 `handshake_overview`：
  - 汇总握手总数、总尝试次数、`pending/consumed/failed/expired` 数量
  - 输出按错误码聚合的失败统计和最近失败记录
  - 返回最近会话列表，便于前端和专家模式直接消费
- 收紧初始化任务创建：
  - `create_bootstrap_task()` 现在会拒绝同一 `source_id` 下已存在的 `pending/running` 任务
  - 返回 `ERR_TASK_ALREADY_ACTIVE` 与现有任务摘要，避免裸调接口时创建并发任务

### 前端

- `WizardMaster` 的安装日志轮询增加请求序号保护
- 切换页面、停止轮询或重新发起安装时，旧日志请求结果会被丢弃，避免晚到响应覆盖当前内容

### 价值

- 握手链路从“单 token 状态”进一步提升到“可聚合、可总览”的视图
- 后端补上同来源任务创建的硬护栏，避免并发初始化
- 主库工具安装日志在慢请求场景下更稳定，不再出现旧轮询覆盖新日志

## 阶段三十一：后端第一批模块化拆分（已完成）

### 拆分范围

- 新增 `mms/` 包，先抽离最稳定的一批基础能力：
  - `mms/validators.py`
  - `mms/crypto.py`
  - `mms/config_store.py`
  - `mms/logging_audit.py`
  - `mms/handshake_service.py`
- `mysql_multi_source_main.py` 保持入口文件和类名不变，仅改为通过 mixin 复用上述实现

### 本轮原则

- 纯搬运，不改接口路由
- 纯搬运，不改数据结构
- 纯搬运，不改业务行为

### 价值

- 先把校验、加密、配置存储、日志审计、profile/handshake 等稳定能力从超大单文件中剥离
- 为后续继续拆 `source/channel/bootstrap/wizard/dashboard` 等业务模块打基础
