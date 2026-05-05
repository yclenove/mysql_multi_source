# FB-002 — 阶段三十三反馈分析报告

> 分析时间：2026-05-05 | 版本：v2.1.0 | 分析师：feedback-analyst
> 分析范围：阶段三十二交付物 + 上轮遗留问题跟踪 + 代码深度审计

## 概览

| 指标 | 值 |
|------|-----|
| 本轮新增反馈 | 7 |
| 上轮遗留未解决 | 8 |
| 总活跃反馈 | 15 |
| 数据来源 | 代码审计、QA 质量门禁报告、上轮反馈跟踪、Git 历史 |
| GitHub Issues/PR | 0（仓库尚无外部 issue/PR，gh CLI 不可用） |

## 上轮遗留问题跟踪

### P1 项（上轮标记为高优）

| ID | 描述 | 阶段三十二状态 | 本轮结论 |
|----|------|---------------|----------|
| FB-004 | GTID purged SQL 注入校验 | **已修复** (ITER-032-01) | 关闭。正则锚定 + 测试覆盖充分 |
| FB-007 | 配置单明文密码泄露 | **已修复** (ITER-032-03) | 关闭。Fernet 加密 + 签名同步 |
| FB-002 | XOR fallback 加密强度不足 | **已修复** (ITER-032-02) | 关闭。写入侧移除、读取侧兼容 |
| FB-008 | MySQL 8.0.23+ 语法适配 | **大部分完成** (ITER-032-05) | 降级为 P2。主文件 9 处已适配，但 dashboard_service.py 残留硬编码 |
| FB-011 | my.cnf 覆盖风险 | **已修复** (ITER-032-06) | 关闭。include 机制 + 备份 |
| FB-012 | 裸 except 分类捕获 | **部分完成** (ITER-032-08) | 保留 P2。主文件仍残留约 70 处 |
| FB-001 | 单文件架构维护瓶颈 | **进展中** | 保留 P1。主文件 3785 行，已拆出 8 个 mixin 模块但主文件仍过大 |
| FB-009 | 复制延迟告警 | **未开始** | 保留 P1。无阈值告警机制 |

### P2 项（上轮标记为中优）

| ID | 描述 | 阶段三十二状态 | 本轮结论 |
|----|------|---------------|----------|
| FB-005 | 接口返回契约统一 | **进展中** | 保留 P2。`public.returnMsg` 66 处 vs `self._ok/_fail` 133 处，mms/ 模块仍有 16 处混用 |
| FB-003 | 跨平台文件锁 | **未开始** | 保留 P2。Windows 下 `_with_lock` 静默失效 |
| FB-017 | MySQL 8.4 新语法支持 | **大部分完成** | 关闭。8.0.23+ 适配已覆盖 8.4 场景 |
| FB-018 | 配置迁移框架 | **未开始** | 保留 P2。仅有版本号递增，无结构化迁移 |
| FB-015 | 核心逻辑单元测试 | **已完成** (ITER-032-07) | 关闭。236 条用例，mms/ 覆盖率 85% |

### P3 项（Backlog）

| ID | 描述 | 状态 | 结论 |
|----|------|------|------|
| FB-010 | 批量操作 | 未开始 | 保留 P3 |
| FB-013 | 模式切换状态保持 | 未开始 | 保留 P3 |
| FB-014 | 定时巡检 | 未开始 | 保留 P3 |
| FB-016 | 物理备份错误信息优化 | 未开始 | 保留 P3 |

---

## 本轮新发现

### 新增反馈汇总

| ID | 类别 | 描述 | 来源 | 严重度 | 优先级 |
|----|------|------|------|--------|--------|
| FB-019 | Bug | `_all_slave_status()` 硬编码 `SHOW SLAVE STATUS`，未走 `_replication_sql` 适配 | QA 质量门禁 | 中 | P2 |
| FB-020 | 架构 | `wizard_dashboard_snapshot` 在主文件和 dashboard_service.py 中重复定义 | 代码审计 | 中 | P2 |
| FB-021 | Bug | `_with_lock` 在 Windows（`_fcntl is None`）下完全无锁保护，静默跳过 | 代码审计 | 中 | P2 |
| FB-022 | 体验 | `_crypto_key` 在无 cryptography 时生成无用 fallback key 文件，误导用户 | 代码审计 | 低 | P3 |
| FB-023 | 架构 | `diagnose_service.py` 覆盖率仅 34%，核心方法无集成测试 | QA 质量门禁 | 中 | P2 |
| FB-024 | 体验 | `handshake_service.py` 返回风格混用：5 处 `public.returnMsg` + 5 处 `self._ok/_fail` | 代码审计 | 低 | P3 |
| FB-025 | 架构 | 主文件 3785 行，mms/ 总计仅 1131 行，拆分比例仍严重失衡 | 代码审计 | 一般 | P1 |

---

## 详细分析

### FB-019 — `_all_slave_status()` 硬编码旧语法

- **位置**：`mms/dashboard_service.py:91-109`
- **问题**：方法直接拼接 `"SHOW SLAVE STATUS"` 字符串，通过 try/except 回退到 `"SHOW REPLICA STATUS"`。未使用 `replication_syntax.py` 提供的 `_replication_sql("SHOW_STATUS")` 适配函数。
- **影响**：
  1. 违反"所有复制 SQL 统一走适配函数"的设计原则
  2. MySQL 9.0+ 若移除 `SHOW SLAVE STATUS`，当前回退依赖异常，不够健壮
  3. 该方法不按 channel 过滤（返回所有 channel 的状态），无法直接复用 `_replication_sql`（它需要 channel 参数）
- **建议**：扩展 `replication_sql` 函数增加 `cmd="SHOW_STATUS_ALL"` 分支（不带 FOR CHANNEL），或在 mixin 中添加 `_replication_sql_all()` 辅助方法

### FB-020 — `wizard_dashboard_snapshot` 重复定义

- **位置**：`mysql_multi_source_main.py:3625-3700` 与 `mms/dashboard_service.py:129-204`
- **问题**：主文件中定义了一份完整的 `wizard_dashboard_snapshot`，dashboard_service.py mixin 中也定义了一份。由于主类继承了 DashboardServiceMixin，Python MRO 会使用主文件的版本（后定义覆盖先定义），mixin 中的版本成为死代码。
- **影响**：
  1. 两份代码逻辑几乎相同，维护时容易遗漏同步
  2. mixin 中的版本从未被调用，浪费代码量
  3. 增加模块拆分的混乱度
- **建议**：删除主文件中的重复定义，统一使用 mixin 版本

### FB-021 — Windows 下文件锁静默失效

- **位置**：`mysql_multi_source_main.py:103-124`
- **问题**：`_with_lock` 在 `_fcntl is None`（Windows 环境）时直接跳过锁保护，`yield` 照常执行。宝塔面板有 Windows 版本，若在 Windows 上运行此插件，并发配置写入可能导致 JSON 损坏。
- **影响**：Windows 用户并发操作时配置文件可能写坏
- **建议**：
  - 方案 A：引入 `portalocker` 库（跨平台文件锁）
  - 方案 B：使用 `msvcrt.locking` 作为 Windows fallback
  - 方案 C：至少在无锁环境下记录 warning 日志

### FB-022 — 无用 fallback key 文件

- **位置**：`mms/crypto.py:35-38`
- **问题**：当 `cryptography` 库未安装时，`_crypto_key()` 仍会生成一个基于 `os.urandom(32)` 的 key 文件。但 `_crypto_encrypt` 在无 Fernet 时直接抛 RuntimeError，所以这个 key 永远不会被用于加密。用户看到 `secret.key` 文件存在可能误以为加密正常工作。
- **建议**：无 Fernet 时不生成 key 文件，或在 key 文件中写入注释说明加密不可用

### FB-023 — `diagnose_service.py` 测试覆盖不足

- **位置**：`tests/test_diagnose_service.py`（76 行）
- **问题**：仅测试了 `_classify_error` 和 `_classify_connectivity_error` 两个纯函数。`diagnose_source`、`wizard_diagnose_all`、`wizard_quick_fix` 三个核心业务方法均未覆盖。QA 报告显示覆盖率仅 34%。
- **影响**：诊断功能的回归风险高，重构时缺乏安全网
- **建议**：补充集成测试，mock `_all_slave_status`、`test_source_connection`、`master_health_check` 等依赖

### FB-024 — handshake_service 返回风格混用

- **位置**：`mms/handshake_service.py`
- **问题**：
  - `master_export_signed_profile`、`master_get_profile`：使用 `public.returnMsg(True/False, ...)`
  - `replica_verify_profile`、`replica_import_profile`、`master_create_handshake` 等：使用 `self._ok/_fail`
- **影响**：前端需要同时处理两种返回格式，增加解析复杂度
- **建议**：统一为 `self._ok/_fail` 结构化返回

### FB-025 — 主文件与 mixin 拆分比例失衡

- **当前状态**：
  - `mysql_multi_source_main.py`：3785 行
  - `mms/` 总计：1131 行（config_store 109 + crypto 96 + dashboard 204 + diagnose 150 + handshake 329 + logging 51 + replication 161 + validators 31）
  - 拆分比例：约 23% 在 mixin，77% 在主文件
- **问题**：虽然已拆出 8 个 mixin，但主文件仍承载大量业务逻辑（source CRUD、bootstrap 流程、复制管理、向导编排、SSH 远程操作等）
- **建议**：下个迭代继续拆分，优先拆出 `source_manager`、`bootstrap_manager`、`replication_manager` 三个 mixin

---

## 质量门禁遗留项跟踪

来自 QA-质量门禁报告.md 的建议改进项：

| 编号 | 描述 | 状态 | 说明 |
|------|------|------|------|
| FIX-01 | `_all_slave_status` 应走适配路径 | **未修复** | 即 FB-019 |
| FIX-02 | `diagnose_service.py` 补充集成测试 | **未修复** | 即 FB-023 |
| SUG-01 | 主文件剩余 ~70 处裸 except | **未修复** | 即 FB-012 遗留 |
| SUG-02 | GTID 正则补充防御性测试 | **未修复** | 低风险，择机处理 |
| SUG-03 | `_crypto_key` 中 `os.chmod` 静默吞异常 | **未修复** | 低风险，择机处理 |
| SUG-04 | CHANGELOG.md 未更新 | **已修复** | 阶段三十二提交中已更新 |

---

## 优先级排序（阶段三十三建议）

### P0 — 立即修复

无 P0 项。

### P1 — 下个迭代必须完成

| ID | 问题 | 预估工时 | 依赖 |
|----|------|----------|------|
| FB-025 | 主文件继续拆分（source_manager / bootstrap_manager / replication_manager） | 5d | 无 |
| FB-009 | 复制延迟告警机制（阈值 + webhook/邮件通知） | 2d | 无 |
| FB-001 | 前端单文件拆分（api/state/render/actions） | 3d | 无 |

### P2 — 下下个迭代

| ID | 问题 | 预估工时 | 依赖 |
|----|------|----------|------|
| FB-019 | `_all_slave_status` 走适配路径 | 0.5d | 无 |
| FB-020 | 删除主文件中重复的 `wizard_dashboard_snapshot` | 0.5d | FB-019 |
| FB-023 | `diagnose_service.py` 补充集成测试 | 1d | 无 |
| FB-012 | 裸 except 继续分类捕获（剩余 ~70 处） | 2d | 无 |
| FB-005 | 接口返回契约统一（`public.returnMsg` → `self._ok/_fail`） | 2d | 无 |
| FB-021 | 跨平台文件锁（Windows fallback） | 1d | 无 |
| FB-018 | 配置迁移框架（版本化 schema migration） | 1d | 无 |

### P3 — Backlog

| ID | 问题 | 说明 |
|----|------|------|
| FB-010 | 批量操作 | 批量启停通道、批量删除来源 |
| FB-013 | 模式切换状态保持 | 新手/专家模式切换后表单状态丢失 |
| FB-014 | 定时巡检 | 定期采集复制健康指标 |
| FB-016 | 物理备份错误信息优化 | xtrabackup prepare 阶段错误详情 |
| FB-022 | 无用 fallback key 文件 | 无 cryptography 时不生成 key |
| FB-024 | handshake_service 返回风格混用 | 统一为 self._ok/_fail |

---

## 反馈模式分析

### 模式一：模块拆分进入深水区

- 阶段三十一拆出首批 mixin，阶段三十二继续拆分，但主文件仍占 77% 代码量
- 重复定义（FB-020）表明拆分过程中存在遗留同步问题
- **信号**：需要更系统化的拆分计划，而非逐方法迁移

### 模式二：安全加固收尾但细节仍需打磨

- XOR fallback、GTID 注入、SSH 密码、配置单加密——四大安全问题已修复
- 但 `_crypto_key` fallback 逻辑（FB-022）、`_with_lock` Windows 失效（FB-021）仍有隐患
- **信号**：安全类问题从"显性漏洞"转入"边界条件"阶段

### 模式三：测试覆盖不均衡

- validators(100%) / replication_syntax(100%) / config_store(92%) 覆盖良好
- diagnose_service(34%) 严重不足，dashboard_service(89%) 尚可但缺边界测试
- **信号**：新拆出的模块优先补测试，老模块的集成测试是短板

---

## 质量门禁自检

| 检查项 | 状态 |
|--------|------|
| >= 5 条有效反馈 | 本轮 7 条新增 + 8 条遗留跟踪 |
| 每条反馈有来源标注 | 全部标注（代码审计 / QA 报告 / 上轮跟踪） |
| 优先级有量化依据 | 工时估算 + 依赖关系 + 严重度 |
| Top 问题有引用 | 含文件路径 + 行号 |
| 反馈模式 >= 2 个主题 | 3 个模式 |
| 上轮遗留问题全部跟踪 | 13 项逐一标注状态 |
