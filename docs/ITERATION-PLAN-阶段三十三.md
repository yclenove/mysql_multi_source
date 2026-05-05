# ITER-033 -- mysql_multi_source 迭代计划 v2.2.0

> 迭代主题：工程化收尾 + 代码清洁 | 版本目标：v2.2.0 | 总工时：6d 开发 + 1d 缓冲 = 7d

---

## 迭代目标

> 修复阶段三十二遗留的 2 项 P0 兼容性/架构问题（MySQL 8.4 LTS 硬编码、方法重复定义），补充诊断模块测试覆盖率，推进裸 except 改造和 SSH 安全加固，统一接口返回契约，完成工程化收尾。

---

## 文档健康检查

- [x] 侦察报告：`docs/SCOUT-阶段三十三侦察报告.md` 存在
- [x] 反馈分析：`docs/FEEDBACK-阶段三十三反馈报告.md` 存在（15 条活跃反馈）
- [x] 理解项目现状：v2.1.0，236 条测试，mms/ 覆盖率 85%，主文件 3785 行
- [x] 上轮迭代：`docs/ITERATION-PLAN-阶段三十二.md` 存在，9 项任务全部完成

---

## 最终任务清单

### Phase 1：兼容性 + 架构修复（并行执行，无依赖）

| ID | 名称 | 来源 | 优先级 | 预估 | 风险 | 范围 |
|----|------|------|--------|------|------|------|
| ITER-033-01 | `_all_slave_status` 走 `_replication_sql` 适配 | FB-019 / FIX-01 | P0 | 0.5d | 中 | mms/replication_syntax.py + mms/dashboard_service.py |
| ITER-033-02 | 消除 3 个方法重复定义（主文件 vs mixin） | FB-020 / TD-9 | P0 | 0.5d | 中 | mysql_multi_source_main.py |

### Phase 2：测试补全 + 安全加固（Phase 1 完成后并行）

| ID | 名称 | 来源 | 优先级 | 预估 | 风险 | 范围 |
|----|------|------|--------|------|------|------|
| ITER-033-03 | 补充 `diagnose_service.py` 测试（覆盖率 34% -> 80%） | FB-023 / FIX-02 | P1 | 1d | 低 | tests/test_diagnose_service.py |
| ITER-033-04 | 裸 except 第二批改造 Top 20 | FB-012 | P1 | 1.5d | 低 | mysql_multi_source_main.py 约 20 处 |
| ITER-033-05 | SSH 主机密钥安全改进 | TD-4 | P1 | 0.5d | 中 | mysql_multi_source_main.py 6 处 SSH 调用 |

### Phase 3：接口契约统一（建议做，可裁剪）

| ID | 名称 | 来源 | 优先级 | 预估 | 风险 | 范围 |
|----|------|------|--------|------|------|------|
| ITER-033-06 | 接口返回契约统一（`public.returnMsg` -> `_ok/_fail`） | FB-005 / FB-024 | P2 | 1.5d | 中 | mms/handshake_service.py + mms/ 约 16 处 |

### Phase 4：跨平台改进（建议做，可裁剪）

| ID | 名称 | 来源 | 优先级 | 预估 | 风险 | 范围 |
|----|------|------|--------|------|------|------|
| ITER-033-07 | `_with_lock` 跨平台锁改进 | FB-003 / FB-021 | P2 | 0.5d | 中 | mysql_multi_source_main.py `_with_lock` 方法 |

---

## 依赖关系与执行顺序

```
Phase 1（兼容性 + 架构修复，可并行）
  ├─ ITER-033-01 _all_slave_status 适配
  └─ ITER-033-02 消除方法重复定义
        │
        │ （02 完成后，mixin 中的方法成为实际运行版本，
        │  03 的测试才能覆盖真实代码路径）
        v
Phase 2（测试 + 安全，01/02 完成后可并行）
  ├─ ITER-033-03 diagnose_service 测试补全
  ├─ ITER-033-04 裸 except 改造 Top 20
  └─ ITER-033-05 SSH 主机密钥安全改进
        │
        v
Phase 3（接口契约，可与 Phase 2 并行，但建议在其后）
  └─ ITER-033-06 returnMsg -> _ok/_fail 统一

Phase 4（跨平台，可与 Phase 3 并行）
  └─ ITER-033-07 _with_lock 跨平台锁
```

**关键路径**：01/02（并行）-> 03 -> 06

**注意**：ITER-033-02 必须在 ITER-033-03 之前完成。原因：当前主文件中的重复定义覆盖了 mixin 版本（Python MRO），如果先补测试再删重复，测试覆盖的是 mixin 中的死代码版本，而非实际运行版本。删除重复后，mixin 版本才成为运行时生效的版本，此时补测试才有意义。

---

## 详细验收标准

### ITER-033-01：`_all_slave_status` 走 `_replication_sql` 适配

**Given** MySQL 版本 >= 8.4 LTS（已移除 SHOW SLAVE STATUS）
**When** 调用 `_all_slave_status()`
**Then** 直接使用 `SHOW REPLICA STATUS` 语法，不触发无效 SQL 异常

验收：
- [ ] `mms/replication_syntax.py` 中 `replication_sql()` 新增 `cmd="SHOW_STATUS_ALL"` 分支（不带 FOR CHANNEL）
- [ ] `mms/dashboard_service.py:_all_slave_status()` 改为调用 `self._replication_sql("SHOW_STATUS_ALL")`
- [ ] MySQL 5.7 下仍使用 `SHOW SLAVE STATUS`
- [ ] MySQL 8.0.23+ / 8.4 下使用 `SHOW REPLICA STATUS`
- [ ] 不再有 try/except fallback 模式——版本检测在调用前完成
- [ ] `pytest tests/test_replication_syntax.py` 新增 SHOW_STATUS_ALL 用例通过
- [ ] `pytest tests/` 全量通过，无回归

### ITER-033-02：消除 3 个方法重复定义

**Given** 主文件中存在 `wizard_dashboard_snapshot`、`wizard_diagnose_all`、`wizard_quick_fix` 的重复定义
**When** 删除主文件中的重复定义
**Then** Python MRO 自动使用 mixin 中的版本，行为不变

验收：
- [ ] `mysql_multi_source_main.py` 中删除以下 3 个方法的完整定义：
  - `wizard_dashboard_snapshot`（L3625-3700 附近）
  - `wizard_diagnose_all`（L3702-3770 附近）
  - `wizard_quick_fix`（L3770+ 附近）
- [ ] 主类仍通过 mixin 继承获得这 3 个方法（无 AttributeError）
- [ ] `grep -n "def wizard_dashboard_snapshot\|def wizard_diagnose_all\|def wizard_quick_fix" mysql_multi_source_main.py` 返回空
- [ ] `grep -rn "def wizard_dashboard_snapshot\|def wizard_diagnose_all\|def wizard_quick_fix" mms/` 返回 mixin 中的定义
- [ ] 主文件行数减少约 150 行
- [ ] `pytest tests/` 全量通过

### ITER-033-03：补充 `diagnose_service.py` 测试

**Given** `diagnose_service.py` 覆盖率仅 34%
**When** 补充集成测试
**Then** 覆盖率提升至 80%+

验收：
- [ ] `tests/test_diagnose_service.py` 新增测试用例覆盖以下方法：
  - `diagnose_source` — mock `_get_source_status`、`test_source_connection`、`get_gtid_status`，验证正常/异常/超时场景
  - `wizard_diagnose_all` — mock `_all_slave_status`、`master_health_check`，验证多 channel 分类（healthy/degraded/error）
  - `wizard_quick_fix` — mock `master_auto_fix_apply`、`recover_bootstrap_tasks`，验证修复动作触发
- [ ] `pytest --cov=mms.diagnose_service --cov-report=term-missing` 覆盖率 >= 80%
- [ ] 所有测试可在无 MySQL 实例环境下运行（纯 mock）
- [ ] `pytest tests/` 全量通过

### ITER-033-04：裸 except 第二批改造 Top 20

**Given** 主文件中仍有 70 处 `except Exception:`
**When** 改造 SSH 和数据库操作相关的 Top 20 处
**Then** 改为分类捕获 + 结构化日志

验收：
- [ ] 从以下行号范围选取 20 处改造（优先 SSH 和数据库操作）：
  - SSH/远程操作：L650, L685, L697, L771, L820, L2112 区域
  - 数据库操作：L1456, L1474, L1542, L1626 区域
  - 向导/诊断：L2850, L2983, L3011, L3125, L3131, L3142, L3150, L3159, L3172, L3181, L3184, L3192, L3248, L3272, L3324, L3361, L3389, L3470, L3760 区域
- [ ] 改造模式：`except (具体异常类型1, 具体异常类型2) as e:` + `logger.warning/error("上下文: %s", e)`
- [ ] 不改变原有业务逻辑行为（异常仍被捕获，不向上抛出）
- [ ] `pytest tests/` 全量通过
- [ ] `flake8` 无新增 warning

### ITER-033-05：SSH 主机密钥安全改进

**Given** 6 处 SSH 调用使用 `StrictHostKeyChecking=no`
**When** 改进主机密钥验证策略
**Then** 提供配置化选项，默认记录 warning 而非静默跳过

验收：
- [ ] 新增配置项 `ssh_strict_host_key`（默认 `accept-new`，可选 `yes`/`no`/`accept-new`）
  - `accept-new`：首次连接自动接受，后续验证（推荐，兼顾安全和易用性）
  - `yes`：严格验证（需用户手动添加主机密钥）
  - `no`：跳过验证（当前行为，向后兼容）
- [ ] 6 处 SSH 调用统一读取该配置项
- [ ] 配置为 `no` 时记录 `logger.warning("SSH 主机密钥验证已禁用，存在 MITM 风险")`
- [ ] 默认 `accept-new` 模式下功能回归：SSH 远程命令正常执行
- [ ] `pytest tests/` 全量通过

### ITER-033-06：接口返回契约统一

**Given** mms/ 模块中 `public.returnMsg` 和 `self._ok/_fail` 混用
**When** 统一为 `self._ok/_fail` 结构化返回
**Then** 所有 mms/ 模块方法返回格式一致

验收：
- [ ] `mms/handshake_service.py` 中 5 处 `public.returnMsg` 改为 `self._ok/_fail`
- [ ] mms/ 其他模块中残留的 `public.returnMsg` 调用一并清理
- [ ] `grep -rn "public.returnMsg" mms/` 返回空
- [ ] `_ok` 返回格式：`{"success": True, "data": ...}`
- [ ] `_fail` 返回格式：`{"success": False, "msg": "..."}`
- [ ] 前端解析逻辑无需变更（`_ok/_fail` 格式与 `returnMsg` 兼容）
- [ ] `pytest tests/` 全量通过

### ITER-033-07：`_with_lock` 跨平台锁改进

**Given** `_with_lock` 在 Windows（`_fcntl is None`）下无锁保护
**When** 改进跨平台锁实现
**Then** Windows 下使用 `msvcrt.locking` 作为 fallback

验收：
- [ ] `_with_lock` 在 `_fcntl is None` 时尝试 `msvcrt.locking`
- [ ] Windows 下文件锁生效，并发写入不会损坏 JSON 配置
- [ ] 无 `msvcrt` 的非 Windows 环境（如某些容器）记录 warning 日志并降级为无锁
- [ ] Linux 下行为不变（仍使用 `fcntl.flock`）
- [ ] `pytest tests/` 全量通过

---

## 影响分析

| 维度 | 影响 | 风险等级 |
|------|------|----------|
| 代码 | Phase 1 修改 mms/ 2 个文件 + 主文件删除约 150 行；Phase 2 修改主文件约 20 处 + 新增测试；Phase 3 修改 mms/ 约 16 处 | 中 |
| 数据 | 无 schema 变更 | 低 |
| API | Phase 3 统一返回格式，但 `_ok/_fail` 与 `returnMsg` 结构兼容，前端无需适配 | 低 |
| 测试 | 新增 diagnose_service 测试，replication_syntax 测试扩展 | 低 |
| 文档 | 需更新 CHANGELOG.md | 低 |

---

## 风险清单

| 风险 | 概率 | 影响 | 缓解方案 |
|------|------|------|----------|
| 删除主文件重复方法后 mixin 版本行为不一致 | 中 | 高 | 删除前 diff 对比主文件和 mixin 版本，确认逻辑一致后再删除；不一致时以主文件版本为准同步到 mixin |
| `SHOW_STATUS_ALL` 无 channel 过滤返回结果集过大 | 低 | 低 | 当前 `_all_slave_status` 已有 channel 解析逻辑，结果集大小不受影响 |
| SSH `accept-new` 模式在某些旧版 OpenSSH 不支持 | 低 | 中 | 检测 OpenSSH 版本，< 7.6 时 fallback 到 `no` 并记录 warning |
| 裸 except 改造误改业务逻辑 | 低 | 中 | 每处改造后运行对应测试；无测试覆盖的区域保守处理（仅添加日志，不改异常类型） |
| `returnMsg` -> `_ok/_fail` 影响前端解析 | 低 | 中 | 先验证两种返回格式的 JSON 结构兼容性；Phase 3 可裁剪 |

---

## 回滚方案

### ITER-033-01（`_all_slave_status` 适配）

**触发条件**：MySQL 5.7 或 8.0 下 `_all_slave_status` 返回空结果

**回滚步骤**：
1. `git revert <commit-hash>` 回退 replication_syntax.py 和 dashboard_service.py 的变更
2. 重启插件服务
3. 验证：仪表盘 overview_metrics 正常返回 channel 状态

**回滚时间**：5 分钟（纯代码回滚）

### ITER-033-02（消除方法重复）

**触发条件**：删除重复定义后 AttributeError 或行为异常

**回滚步骤**：
1. `git revert <commit-hash>` 恢复主文件中的方法定义
2. 验证：`wizard_dashboard_snapshot`、`wizard_diagnose_all`、`wizard_quick_fix` 功能正常

**回滚时间**：5 分钟

**注意**：如果回滚 02，03 的测试也需要同步回滚（测试覆盖的是 mixin 版本，回滚后主文件版本重新覆盖 mixin）。

### ITER-033-05（SSH 主机密钥）

**触发条件**：SSH 连接失败（`accept-new` 不兼容）

**回滚步骤**：
1. 将配置项 `ssh_strict_host_key` 设为 `no`（恢复原行为）
2. 或 `git revert <commit-hash>`
3. 验证：SSH 远程命令正常执行

**回滚时间**：2 分钟（配置修改）或 5 分钟（代码回滚）

### ITER-033-06（返回契约统一）

**触发条件**：前端解析异常

**回滚步骤**：
1. `git revert <commit-hash>`
2. 验证：前端页面功能正常

**回滚时间**：5 分钟

### 其他任务（ITER-033-03/04/07）

**触发条件**：不适用（测试代码/日志改进/锁改进不影响核心业务）

**回滚步骤**：`git revert <commit-hash>`

**回滚时间**：1-5 分钟

---

## 迭代容错

| 场景 | 处理方式 |
|------|----------|
| ITER-033-02 删除重复方法后 mixin 版本有差异 | 以主文件版本为准，将差异同步到 mixin 后再删除主文件版本 |
| ITER-033-03 测试补全超期（1d 不够） | 仅完成 `diagnose_source` 测试（覆盖最核心路径），其余排入下迭代 |
| ITER-033-04 裸 except 改造超期 | 仅改造 SSH 相关 6 处 + 数据库操作 10 处，向导/诊断区域排入下迭代 |
| ITER-033-06 返回契约统一引发前端兼容问题 | 降级为仅统一 handshake_service.py 的 5 处，其他模块排入下迭代 |
| Phase 3/4 整体延期 | 全部移入下迭代，不影响 P0/P1 核心目标 |

---

## 版本策略

- 当前版本：v2.1.0
- 目标版本：**v2.2.0**（minor 升级：兼容性修复 + 测试补全 + 代码清洁）
- 不涉及 breaking change（返回格式兼容，SSH 配置有默认值）

---

## 发布检查清单

- [ ] Phase 1 全部 2 项 P0 修复完成并通过测试
- [ ] Phase 2 测试补全 + 安全加固完成
- [ ] `_all_slave_status` 在 MySQL 5.7 / 8.0 / 8.4 下均有测试覆盖
- [ ] 主文件中无重复方法定义（`grep` 验证）
- [ ] `diagnose_service.py` 覆盖率 >= 80%
- [ ] `pytest tests/` 全量通过（236+ 条）
- [ ] `flake8` 无新增 warning
- [ ] SSH 配置项 `ssh_strict_host_key` 默认值为 `accept-new`
- [ ] CHANGELOG.md 已更新至 v2.2.0
- [ ] 回滚方案已文档化（本文档）
- [ ] CI 流水线绿色

---

## 进度追踪

| 任务 | 状态 | 阻塞 | 预计完成 |
|------|------|------|----------|
| ITER-033-01 _all_slave_status 适配 | 待开始 | 无 | Day 1 |
| ITER-033-02 消除方法重复定义 | 待开始 | 无 | Day 1 |
| ITER-033-03 diagnose_service 测试补全 | 待开始 | 02 完成 | Day 2 |
| ITER-033-04 裸 except 改造 Top 20 | 待开始 | 无 | Day 3-4 |
| ITER-033-05 SSH 主机密钥安全改进 | 待开始 | 无 | Day 3 |
| ITER-033-06 返回契约统一 | 待开始 | 无 | Day 5-6 |
| ITER-033-07 _with_lock 跨平台锁 | 待开始 | 无 | Day 6 |

---

## 质量门禁自检

- [x] 每项有验收标准（Given/When/Then）
- [x] 影响分析覆盖代码/数据/API/测试/文档
- [x] 每个功能有回滚方案
- [x] 回滚方案有触发条件和验证步骤
- [x] 版本号符合语义化规范（v2.1.0 -> v2.2.0）
- [x] 迭代容量不超过可用开发人天（6d 开发 + 1d 缓冲 = 7d）
- [x] 依赖关系明确，关键路径已标注
