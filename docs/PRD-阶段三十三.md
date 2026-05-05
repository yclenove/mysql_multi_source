# PRD-阶段三十三 — 工程化收尾与代码清洁

> 产品：mysql_multi_source | 版本：v2.1.0 -> v2.2.0 | 日期：2026-05-05 | 作者：product-manager

---

## 产品概述

本轮迭代聚焦**工程化收尾**和**代码清洁**，不引入大的新功能。核心任务是修复阶段三十二遗留的 2 项中风险质量门禁问题（`_all_slave_status` 硬编码旧语法、`diagnose_service.py` 测试覆盖不足），消除模块化拆分过程中产生的方法重复死代码，以及推进裸 except 改造和 SSH 安全加固。

**一句话定位**：把工程债务还清，让代码质量从"能跑"升级到"可维护"。

---

## 迭代目标

| # | 目标 | 对应反馈 | 验证方式 |
|---|------|----------|----------|
| G1 | 消除 `_all_slave_status` 硬编码，所有复制 SQL 统一走适配函数 | FB-019, FIX-01, TD-8 | MySQL 8.4 环境下仪表盘无异常 SQL 触发 |
| G2 | 消除主文件与 mixin 的 3 处方法重复，统一走 mixin 版本 | FB-020, TD-9 | 代码审查确认主文件无重复定义，mixin 版本被实际调用 |
| G3 | `diagnose_service.py` 测试覆盖率从 34% 提升至 80%+ | FB-023, FIX-02 | pytest --cov 报告 |
| G4 | 裸 except 第二批改造（SSH + 数据库操作区域约 35 处） | FB-012, SUG-01 | 主文件裸 except 总数降至 35 处以下 |
| G5 | SSH StrictHostKeyChecking 改进，降低中间人攻击风险 | FB-032 | SSH 连接具备主机密钥验证能力 |

---

## 用户故事

| ID | 角色 | 需求 | 价值 | 优先级 |
|----|------|------|------|--------|
| US-01 | DBA | 我希望在 MySQL 8.4 环境下仪表盘和诊断功能不触发任何废弃语法异常，以便生产环境日志干净 | 兼容性 | P0 |
| US-02 | 开发者 | 我希望 mixin 中的方法是实际生效的代码而非死代码，以便修改 mixin 后行为能正确变化 | 可维护性 | P0 |
| US-03 | 开发者 | 我希望诊断模块有充分的测试覆盖，以便重构时有安全网 | 工程质量 | P0 |
| US-04 | 运维人员 | 我希望 SSH 连接具备主机密钥验证能力，以便防止跨机器复制场景下的中间人攻击 | 安全合规 | P1 |
| US-05 | 开发者 | 我希望异常捕获尽可能精确，以便出错时能快速定位问题而非被泛化异常吞掉 | 可调试性 | P1 |

---

## 功能清单

### F1: `_all_slave_status` 适配函数改造（FB-019 / FIX-01 / TD-8）— P0

**背景**：`mms/dashboard_service.py:91-109` 的 `_all_slave_status()` 方法硬编码 `"SHOW SLAVE STATUS"` + try/except 回退到 `"SHOW REPLICA STATUS"`。MySQL 8.4 LTS 已完全移除旧语法，当前机制每次调用都触发一次无效 SQL 异常。

| 子项 | 描述 | 预估工时 |
|------|------|----------|
| F1-a 扩展 `_replication_sql` | 在 `mms/replication_syntax.py` 中增加 `cmd="SHOW_STATUS_ALL"` 分支，不带 `FOR CHANNEL` 子句，返回所有 channel 的状态 | 1h |
| F1-b 改造 `_all_slave_status` | `mms/dashboard_service.py` 中 `_all_slave_status()` 改为调用 `self._replication_sql("SHOW_STATUS_ALL")`，移除硬编码和 try/except 回退 | 0.5h |
| F1-c 同步主文件调用点 | 主文件中通过 `_all_slave_status` 间接调用的 3 处（`overview_metrics`、`wizard_dashboard_snapshot`、`wizard_diagnose_all`）自动受益，无需额外改动；验证无遗漏 | 0.5h |

**验收标准：**

```
F1-a 扩展适配函数
Given MySQL 版本为 8.4.0
When  调用 _replication_sql("SHOW_STATUS_ALL")
Then  返回 "SHOW REPLICA STATUS"（无 FOR CHANNEL），不触发异常

Given MySQL 版本为 5.7.35
When  调用 _replication_sql("SHOW_STATUS_ALL")
Then  返回 "SHOW SLAVE STATUS"，行为不变

F1-b 改造 _all_slave_status
Given 仪表盘刷新触发 overview_metrics
When  MySQL 版本为 8.4.0
Then  日志中无 "SHOW SLAVE STATUS" 相关异常或废弃警告
      且仪表盘数据正常返回
```

### F2: 消除方法重复（FB-020 / TD-9）— P0

**背景**：主文件 `mysql_multi_source_main.py` 中 3 个方法与 mms/ mixin 中的方法完全重复。由于 Python MRO，主文件定义覆盖 mixin 版本，mixin 中的版本成为死代码。

| 方法 | 主文件位置 | mixin 位置 | 行动 |
|------|-----------|-----------|------|
| `wizard_dashboard_snapshot` | L3625-3700 | `mms/dashboard_service.py:129-204` | 删除主文件版本 |
| `wizard_diagnose_all` | L3702-3768 | `mms/diagnose_service.py:68-133` | 删除主文件版本 |
| `wizard_quick_fix` | L3770-3785 | `mms/diagnose_service.py:135-150` | 删除主文件版本 |

| 子项 | 描述 | 预估工时 |
|------|------|----------|
| F2-a 逐方法对比 | 对比主文件版本与 mixin 版本的差异，确认 mixin 版本功能完整（如有差异，将主文件的增量逻辑合入 mixin） | 1h |
| F2-b 删除主文件重复 | 从主文件中删除 3 个方法定义 | 0.5h |
| F2-c 回归验证 | 确认删除后仪表盘快照、全局诊断、一键修复功能正常 | 1h |

**验收标准：**

```
F2-b 删除重复
Given 主文件 mysql_multi_source_main.py
When  搜索 "def wizard_dashboard_snapshot" / "def wizard_diagnose_all" / "def wizard_quick_fix"
Then  主文件中无此 3 个方法定义
      且 mms/ mixin 中的版本被实际调用（可通过日志或断点验证）

F2-c 回归验证
Given 仪表盘页面
When  点击"刷新快照"
Then  数据正常返回，来源为 mixin 中的 wizard_dashboard_snapshot

Given 诊断页面
When  执行"全局诊断"
Then  诊断结果正常返回，来源为 mixin 中的 wizard_diagnose_all

Given 诊断结果为可修复错误
When  点击"一键修复"
Then  修复流程正常执行，来源为 mixin 中的 wizard_quick_fix
```

### F3: 补充 diagnose_service 测试（FB-023 / FIX-02）— P0

**背景**：`tests/test_diagnose_service.py` 仅 76 行，覆盖率 34%。`diagnose_source`、`wizard_diagnose_all`、`wizard_quick_fix` 三个核心业务方法均未覆盖。

| 子项 | 描述 | 预估工时 |
|------|------|----------|
| F3-a `diagnose_source` 测试 | mock `_get_source_status`、`test_source_connection`、`get_gtid_status`，覆盖：源正常、源断连、复制延迟、GTID 不一致等场景 | 2h |
| F3-b `wizard_diagnose_all` 测试 | mock `_all_slave_status`、`master_health_check`，覆盖：全部正常、部分异常、全部异常、混合错误分类 | 2h |
| F3-c `wizard_quick_fix` 测试 | mock `master_auto_fix_apply`、`recover_bootstrap_tasks`，覆盖：修复成功、部分成功、全部失败、无可修复项 | 1.5h |
| F3-d `_all_slave_status` 适配后测试 | 验证改造后的 `_all_slave_status` 走 `_replication_sql` 路径 | 0.5h |

**验收标准：**

```
Given 项目根目录执行 pytest tests/test_diagnose_service.py --cov=mms.diagnose_service --cov-report=term-missing
When  测试全部运行
Then  mms/diagnose_service.py 覆盖率 >= 80%
      且全部测试通过，无失败
```

### F4: 裸 except 第二批改造（FB-012 / SUG-01）— P1

**背景**：主文件中仍有约 70 处 `except Exception:` 裸捕获。阶段三十二已改造约 15 处。本轮聚焦 SSH 和数据库操作区域。

| 区域 | 行号范围 | 约处数 | 改造策略 |
|------|---------|--------|----------|
| SSH/远程操作 | L650-830, L2100-2120 | ~15 | 拆分为 `except (paramiko.SSHException, socket.error) as e` + `except Exception as e: logger.warning(...)` |
| 数据库操作 | L930-1900 | ~20 | 拆分为 `except pymysql.Error as e` + `except Exception as e: logger.warning(...)` |

| 子项 | 描述 | 预估工时 |
|------|------|----------|
| F4-a SSH 区域改造 | 物理备份远程命令、SSH 通用执行等 ~15 处，改为具体异常类型 + 日志 | 2h |
| F4-b 数据库区域改造 | 复制管理、通道操作等 ~20 处，改为 `pymysql.Error` + 日志 | 2h |

**验收标准：**

```
Given 主文件 mysql_multi_source_main.py
When  统计 "except Exception:" 的数量
Then  总数从 70 处降至 35 处以下
      且改造后的异常捕获均带具体异常类型或 as e 日志记录
      且 pytest 全部通过，无回归
```

### F5: SSH StrictHostKeyChecking 改进（ITER-032 遗留）— P1

**背景**：主文件中 6 处 SSH 连接使用 `StrictHostKeyChecking=no`，跳过主机密钥验证，存在中间人攻击风险。

| 子项 | 描述 | 预估工时 |
|------|------|----------|
| F5-a 引入 known_hosts 机制 | 首次连接时自动记录远程主机密钥到 `~/.ssh/known_hosts`（或项目自定义路径），后续连接启用验证 | 2h |
| F5-b 配置项 `ssh_strict_host_key` | 在配置文件中新增 `ssh_strict_host_key` 选项，默认 `true`（严格验证），用户可设为 `false` 回退到旧行为 | 1h |
| F5-c 6 处统一改造 | 将 6 处 `StrictHostKeyChecking=no` 替换为基于配置项的动态参数 | 1h |

**验收标准：**

```
F5-a 首次连接
Given 远程主机 A 从未连接过
When  插件首次 SSH 连接主机 A
Then  主机密钥被记录到 known_hosts 文件
      且连接成功

F5-a 后续连接
Given 主机 A 的密钥已记录在 known_hosts 中
When  插件再次 SSH 连接主机 A
Then  SSH 启用主机密钥验证（不使用 StrictHostKeyChecking=no）

F5-b 配置回退
Given 配置文件中 ssh_strict_host_key = false
When  插件 SSH 连接远程主机
Then  使用 StrictHostKeyChecking=no（旧行为兼容）
```

---

## 不在范围内（Scope Out）

以下项目本轮**不做**，明确记录以便后续迭代排入：

| 项目 | 原因 | 建议排期 |
|------|------|----------|
| source_manager 模块化拆分 | 工期 3d+，风险高，需系统化规划而非逐方法迁移 | 阶段三十四 |
| 复制延迟告警机制（FB-009） | 新功能，工程化收尾优先于功能扩展 | 阶段三十四 |
| 前端单文件拆分（FB-001 前端部分） | 前端重构需独立迭代 | 阶段三十四/三十五 |
| 接口返回契约统一（FB-005） | 涉及面广（199 处），需逐模块验证 | 阶段三十四 |
| 跨平台文件锁（FB-021） | 需引入外部依赖或平台特定代码，需独立评估 | 阶段三十四 |
| 配置迁移框架（FB-018） | 结构化 schema migration 需设计评审 | 阶段三十五 |
| CHANGELOG.md（SUG-04） | 阶段三十二已更新，本轮结束后统一更新 | 阶段三十三后期 |

---

## RICE 优先级评估

| 功能 | Reach | Impact | Confidence | Effort | RICE 分 | 优先级 |
|------|-------|--------|------------|--------|---------|--------|
| F1: _all_slave_status 适配 | 100% | 3 | 100% | 0.5d | 600 | P0 |
| F2: 消除方法重复 | 100% | 2 | 100% | 0.5d | 400 | P0 |
| F3: diagnose 测试补充 | 100% | 2 | 100% | 1d | 200 | P0 |
| F4: 裸 except 改造 | 100% | 1 | 100% | 1d | 100 | P1 |
| F5: SSH 主机密钥验证 | 60% | 2 | 90% | 1d | 108 | P1 |

---

## 非功能需求

| 维度 | 要求 | 指标 |
|------|------|------|
| 兼容性 | 支持 MySQL 5.7 / 8.0 / 8.4 三个主要版本 | `_all_slave_status` 在三个版本下均不触发废弃语法异常 |
| 可维护性 | 主文件中无与 mixin 重复的方法定义 | `grep "def wizard_"` 在主文件中返回 0 结果 |
| 测试覆盖 | `diagnose_service.py` 覆盖率 >= 80% | pytest --cov 报告 |
| 代码质量 | 主文件裸 except 总数 <= 35 处 | `grep -c "except Exception:"` 统计 |
| 安全 | SSH 连接默认启用主机密钥验证 | 配置项 `ssh_strict_host_key` 默认 true |
| 向后兼容 | 现有配置文件无需手动修改即可升级 | 升级后所有功能正常 |

---

## 产品路线图

| 阶段 | 主题 | 核心交付 | 预估总工时 |
|------|------|----------|------------|
| 三十二（已完成） | 安全加固 + 兼容性 | SQL 注入防护、密码加密、MySQL 8.0.23+ 语法、首批测试 | 6d |
| **三十三（本轮）** | **工程化收尾 + 代码清洁** | **适配函数改造、方法去重、诊断测试、裸 except、SSH 安全** | **5d** |
| 三十四 | 模块化拆分 + 体验优化 | source_manager 拆分、延迟告警、接口契约统一、前端拆分 | 10d |
| 三十五 | 生态扩展 | 配置迁移框架、批量操作、定时巡检 | 5d |

---

## 验收检查清单

- [ ] `_all_slave_status` 调用 `_replication_sql("SHOW_STATUS_ALL")`，无硬编码旧语法
- [ ] MySQL 8.4 环境下仪表盘刷新无废弃语法异常
- [ ] 主文件中无 `wizard_dashboard_snapshot`、`wizard_diagnose_all`、`wizard_quick_fix` 方法定义
- [ ] 仪表盘快照、全局诊断、一键修复功能正常（走 mixin 版本）
- [ ] `mms/diagnose_service.py` 测试覆盖率 >= 80%
- [ ] 主文件裸 except 总数 <= 35 处
- [ ] SSH 连接默认启用主机密钥验证（`ssh_strict_host_key` 配置项生效）
- [ ] pytest 全部通过，无回归
- [ ] CI 流水线（GitHub Actions）全绿
