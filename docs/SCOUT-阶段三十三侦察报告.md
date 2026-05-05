# SCOUT-002 — 阶段三十三侦察报告

> 侦察时间：2026-05-05 | 版本：v2.1.0（阶段三十二） | 可信度：代码分析（高）、市场分析（中-高）

---

## 一、上轮修复验证

### 1.1 已完成项确认

| 上轮承诺 | 状态 | 验证结果 |
|----------|------|----------|
| GTID 注入校验 (ITER-032-01) | 已完成 | 正则锚定 + 测试覆盖充分，无回归 |
| XOR Fallback 移除 (ITER-032-02) | 已完成 | 写入侧移除、读取侧向后兼容，设计正确 |
| 配置单密码加密 (ITER-032-03) | 已完成 | `_migrate_config` + `_save_config` 双路径加密，签名同步更新 |
| SSH 密码泄露修复 (ITER-032-04) | 已完成 | 远程命令改用 `MYSQL_PWD` 环境变量传递 |
| MySQL 8.0.23+ 语法适配 (ITER-032-05) | 部分完成 | 主文件 `_get_source_status` 已走 `_replication_sql`；但 `_all_slave_status` 未适配（见下方） |
| my.cnf 防覆盖 (ITER-032-06) | 已完成 | 独立 `multi_source.cnf` + `!include` 机制 |
| pytest 测试奠基 (ITER-032-07) | 已完成 | 236 条测试，mms/ 覆盖率 85% |
| 裸 except Top 10 改造 (ITER-032-08) | 已完成 | 但仅改造了约 15 处，剩余 70 处未动 |
| GitHub Actions CI (ITER-032-09) | 已完成 | Python 3.8/3.10/3.12 矩阵 + flake8 + pytest-cov |

### 1.2 上轮修复是否引入新问题

**结论：未引入功能性回归，但暴露了一个模块化遗留问题。**

主文件 `mysql_multi_source_main.py` 中存在 3 个方法与 mms/ mixin 中的方法**完全重复**：

| 方法 | mixin 位置 | 主文件位置 | 说明 |
|------|-----------|-----------|------|
| `wizard_dashboard_snapshot` | `mms/dashboard_service.py:129` | `mysql_multi_source_main.py:3625` | 主文件版本覆盖 mixin 版本 |
| `wizard_diagnose_all` | `mms/diagnose_service.py:68` | `mysql_multi_source_main.py:3702` | 主文件版本覆盖 mixin 版本 |
| `wizard_quick_fix` | `mms/diagnose_service.py:135` | `mysql_multi_source_main.py:3770` | 主文件版本覆盖 mixin 版本 |

这意味着 mixin 中这 3 个方法是**死代码**——由于 Python MRO，主文件的定义优先。模块化拆分不彻底导致了重复。

---

## 二、质量门禁遗留项复查

### 2.1 FIX-01: `_all_slave_status` 未走适配函数 — 仍然存在 [中风险]

**现状**：`mms/dashboard_service.py:91-109` 仍硬编码：

```python
def _all_slave_status(self):
    out = {}
    try:
        rows = self._query_sql("SHOW SLAVE STATUS")  # 硬编码旧语法
    except Exception:
        return out
    if not isinstance(rows, (list, tuple)):
        try:
            rows = self._query_sql("SHOW REPLICA STATUS")  # fallback
        except Exception:
            return out
```

**问题升级**：MySQL 8.4 LTS（2024 年 4 月发布）已**完全移除** `SHOW SLAVE STATUS` 语法。当前的 try/except 回退机制在 MySQL 8.4 上虽然功能上能工作（先抛异常，再 fallback），但：
1. 每次调用都触发一次无效 SQL + 异常，性能浪费
2. 异常类型不区分——连接错误和语法错误被同等吞掉
3. 违反"所有复制 SQL 统一走 `_replication_sql`"的设计原则

**影响范围**：`_all_slave_status` 被 3 处调用——`overview_metrics`、`wizard_dashboard_snapshot`、`wizard_diagnose_all`。仪表盘每次刷新都会触发。

**建议修复**：扩展 `replication_sql` 支持无 channel 的全局查询（`SHOW REPLICA STATUS` 不带 `FOR CHANNEL` 返回所有 channel），或在 mixin 中添加 `_replication_sql_all()` 辅助方法。

### 2.2 FIX-02: `diagnose_service.py` 覆盖率仅 34% — 仍然存在 [中风险]

**现状**：测试文件 `tests/test_diagnose_service.py` 仅 75 行，只覆盖了 `_classify_error` 和 `_classify_connectivity_error` 两个纯函数。

**未覆盖的核心方法**：
- `diagnose_source` — 单源诊断（依赖 `_get_source_status`、`test_source_connection`、`get_gtid_status`）
- `wizard_diagnose_all` — 全局诊断分类（依赖 `_all_slave_status`、`master_health_check`）
- `wizard_quick_fix` — 一键修复（依赖 `master_auto_fix_apply`、`recover_bootstrap_tasks`）

**注意**：由于主文件中存在同名重复方法（见 1.2），mixin 中的这 3 个方法实际是死代码。测试应针对主文件版本或先消除重复。

---

## 三、新发现的问题

### 3.1 [高风险] MySQL 8.4 LTS 兼容性阻断

**背景**：MySQL 8.4 LTS（2024-04）是 Oracle 新 LTS 模型的首个版本，支持至 2029 年。它**完全移除**了旧复制语法：

| 旧语法（已移除） | 新语法（8.4 唯一） |
|-----------------|-------------------|
| `SHOW SLAVE STATUS` | `SHOW REPLICA STATUS` |
| `CHANGE MASTER TO` | `CHANGE REPLICATION SOURCE TO` |
| `START SLAVE` | `START REPLICA` |
| `STOP SLAVE` | `STOP REPLICA` |
| `RESET SLAVE` | `RESET REPLICA` |

**当前状态**：
- `mms/replication_syntax.py` 的 `_replication_sql` 函数已正确处理版本检测，`_get_source_status` 等调用点已走适配路径 ✓
- `mms/dashboard_service.py:_all_slave_status` 仍硬编码旧语法 ✗
- 主文件中 3 个重复方法也调用 `_all_slave_status` ✗

**结论**：对于使用 MySQL 8.4 的宝塔用户，仪表盘和诊断功能会**每次调用都触发一次无效 SQL 异常**，虽然通过 fallback 能工作，但体验差且有隐藏风险。

### 3.2 [中风险] SSH StrictHostKeyChecking=no 未修复

**现状**：主文件中仍有 **6 处** `StrictHostKeyChecking=no`：

| 行号 | 场景 |
|------|------|
| 650 | 物理备份远程 xtrabackup 版本检测 |
| 685 | 物理备份远程 MySQL 版本检测 |
| 697 | 物理备份远程工具版本检测 |
| 771 | 物理备份流式传输管道 |
| 820 | 物理备份失败后读取远程错误日志 |
| 2112 | SSH 通用远程命令执行 |

**风险**：跳过主机密钥验证存在中间人攻击（MITM）风险。对于跨机器复制场景，SSH 连接可能被劫持。

**建议**：首次连接时记录主机密钥，后续连接验证。或至少提供配置项让用户选择是否启用严格验证。

### 3.3 [中风险] 主文件方法重复导致模块化失效

**现状**：主文件 3785 行/106 方法，mms/ 包 1132 行。但 3 个已拆分到 mixin 的方法又在主文件中重新定义，导致：
1. mixin 中的版本成为死代码
2. 修改 mixin 不会影响运行时行为
3. 测试覆盖的是 mixin 版本，不是实际运行的版本

**影响**：`wizard_dashboard_snapshot`、`wizard_diagnose_all`、`wizard_quick_fix` 的任何修改需要同步两处，否则会不一致。

### 3.4 [低风险] 裸 except 改造停滞

**现状**：主文件中仍有 **70 处** `except Exception:` 裸捕获（与上轮 QA 报告一致，未减少）。

**分布**：
- 顶部模块导入（L45/50/56）：3 处，可接受（兼容性 fallback）
- 配置/加密相关：约 10 处
- SSH/远程操作：约 15 处
- 数据库操作：约 20 处
- 向导/诊断：约 22 处

**建议**：按模块分批改造，优先处理 SSH 和数据库操作相关的裸 except，添加具体异常类型和日志。

### 3.5 [低风险] `crypto.py` 中 `os.chmod` 静默吞异常

**现状**：`mms/crypto.py:42-43`：
```python
try:
    os.chmod(self.crypto_key_path, 0o600)
except Exception:
    pass
```

在 Windows 环境或权限受限的容器中，`os.chmod` 可能失败。静默吞掉异常会导致密钥文件权限过松而不被察觉。

**建议**：至少记录 debug 日志：`logger.debug("chmod 600 失败（可能在 Windows 上）: %s", e)`

### 3.6 [低风险] CHANGELOG.md 仍缺失

README.md 已更新至 v2.1.0，但 CHANGELOG.md 未创建。对于开源项目，changelog 是用户了解版本变化的重要入口。

---

## 四、产品健康度更新

### 4.1 健康度总览（对比上轮）

| 维度 | 上轮 (v2.0.0) | 本轮 (v2.1.0) | 变化 | 说明 |
|------|--------------|--------------|------|------|
| 文档 | 7/10 | 8/10 | +1 | README 已更新至 v2.1.0，但仍缺 CHANGELOG |
| 测试 | 1/10 | 7/10 | +6 | 236 条测试，85% 覆盖率，但 diagnose 覆盖不足 |
| 代码质量 | 6/10 | 6/10 | 持平 | 模块化有进展但存在重复，裸 except 未减少 |
| 安全 | 7/10 | 8/10 | +1 | GTID 注入防护 + 密码加密 + SSH 密码修复 |
| 前端 | 7/10 | 7/10 | 持平 | 本轮未涉及前端变更 |
| CI/CD | 2/10 | 7/10 | +5 | GitHub Actions 已配置，Python 3.8/3.10/3.12 矩阵 |
| 兼容性 | 6/10 | 7/10 | +1 | 8.0.23+ 语法适配，但 8.4 LTS 仍有硬编码 |
| **综合** | **5/10** | **7.3/10** | **+2.3** | 安全和工程化大幅提升，兼容性和代码质量仍需改进 |

### 4.2 技术债务更新

| 编号 | 债务项 | 上轮状态 | 本轮状态 | 说明 |
|------|--------|----------|----------|------|
| TD-1 | 零测试覆盖 | 高 | **已解决** | 236 条测试，85% 覆盖率 |
| TD-2 | 主文件 3785 行 | 高 | 高 | 仅增加 82 行，模块化拆分停滞 |
| TD-3 | SSH 密码泄露 | 中 | **已解决** | 改用 MYSQL_PWD 环境变量 |
| TD-4 | SSH StrictHostKeyChecking | 中 | 中 | 6 处未修复 |
| TD-5 | XOR 回退加密 | 低 | **已解决** | 写入侧移除，读取侧向后兼容 |
| TD-6 | README 过时 | 中 | **已解决** | 已更新至 v2.1.0 |
| TD-7 | 无 CI/CD | 中 | **已解决** | GitHub Actions 已配置 |
| TD-8 | MySQL 8.4 硬编码 | — | **新增（高）** | `_all_slave_status` 仍用旧语法 |
| TD-9 | 方法重复 | — | **新增（中）** | 3 个方法在主文件和 mixin 中重复定义 |

---

## 五、市场动态

### 5.1 MySQL 8.4 LTS 影响

MySQL 8.4 LTS（2024-04 发布，支持至 2029-04）是当前生产环境的推荐版本。关键变化：
- **完全移除**旧复制语法（MASTER/SLAVE），仅保留 SOURCE/REPLICA
- 强化 GTID 一致性要求
- 默认 `authentication_policy=caching_sha2_password,,`

**对本项目的影响**：
- `_all_slave_status` 的硬编码在 8.4 上会触发异常（虽有 fallback）
- 新用户如果安装 MySQL 8.4，首次使用可能看到仪表盘短暂报错
- 建议在阶段三十三彻底解决此兼容性问题

### 5.2 宝塔面板生态

- 宝塔 9.x 持续迭代，插件生态稳定
- 未发现宝塔官方内置多源复制功能的迹象
- MySQL 多源复制管理仍是宝塔插件生态的空白地带

### 5.3 竞品动态

- ProxySQL 2.7+ 增强了查询路由能力，但不提供多源复制 GUI
- Orchestrator 专注于拓扑管理，不覆盖初始化编排
- 本项目在"宝塔 + 多源复制 GUI + 向导编排"的定位上仍无直接竞品

---

## 六、阶段三十三行动建议

### P0 — 必须修复

| 编号 | 任务 | 优先级 | 预估工作量 | 说明 |
|------|------|--------|-----------|------|
| ITER-033-01 | 修复 `_all_slave_status` 走 `_replication_sql` 适配 | 高 | 2h | 扩展 `replication_sql` 支持无 channel 的全局查询，或添加 `_replication_sql_all()` 辅助方法 |
| ITER-033-02 | 消除主文件与 mixin 的方法重复 | 高 | 3h | 删除主文件中的 `wizard_dashboard_snapshot`、`wizard_diagnose_all`、`wizard_quick_fix`，统一走 mixin |
| ITER-033-03 | 补充 `diagnose_service.py` 测试至 80%+ | 中 | 4h | 为 `diagnose_source`、`wizard_diagnose_all`、`wizard_quick_fix` 补集成测试 |

### P1 — 建议修复

| 编号 | 任务 | 优先级 | 预估工作量 | 说明 |
|------|------|--------|-----------|------|
| ITER-033-04 | SSH StrictHostKeyChecking 改进 | 中 | 3h | 首次连接记录主机密钥，后续验证；或提供配置项 |
| ITER-033-05 | 裸 except 改造第二批（SSH/数据库操作） | 低 | 4h | 主文件中 SSH 和数据库操作相关的 ~35 处裸 except |
| ITER-033-06 | `crypto.py` chmod 异常日志 | 低 | 0.5h | `os.chmod` 失败时记录 debug 日志 |
| ITER-033-07 | 创建 CHANGELOG.md | 低 | 1h | 记录 v2.0.0 至 v2.1.0 的版本变化 |

### P2 — 持续关注

| 编号 | 任务 | 说明 |
|------|------|------|
| ONGOING-01 | 后端主文件模块化拆分 | 主文件仍 3785 行，source/channel/bootstrap/wizard 待拆分 |
| ONGOING-02 | 复制延迟告警机制 | 用户旅程最大摩擦点：缺乏主动告警通知 |
| ONGOING-03 | 备份与多源复制冲突 | 宝塔备份恢复后 channel 配置可能丢失 |
| ONGOING-04 | 前端 Vitest 组件测试 | 前端零测试覆盖 |

---

## 七、附录

### 7.1 代码统计

| 指标 | 上轮 (v2.0.0) | 本轮 (v2.1.0) | 变化 |
|------|--------------|--------------|------|
| 主文件行数 | 3703 | 3785 | +82 |
| 主文件方法数 | 105 | 106 | +1 |
| mms/ 包行数 | 944 | 1132 | +188 |
| 测试文件数 | 0 | 8 | +8 |
| 测试用例数 | 0 | 236 | +236 |
| mms/ 覆盖率 | 0% | 85% | +85% |

### 7.2 裸 except 行号分布

主文件中 70 处 `except Exception:` 的行号：
`45, 50, 56, 111, 119, 123, 154, 330, 471, 483, 541, 595, 692, 705, 711, 804, 830, 930, 1069, 1191, 1456, 1474, 1542, 1626, 1788, 1794, 1798, 1810, 1836, 1853, 1898, 1905, 1924, 1981, 1986, 1996, 2000, 2038, 2064, 2072, 2100, 2105, 2177, 2305, 2374, 2393, 2487, 2546, 2626, 2648, 2673, 2850, 2983, 3011, 3125, 3131, 3142, 3150, 3159, 3172, 3181, 3184, 3192, 3248, 3272, 3324, 3361, 3389, 3470, 3760`

### 7.3 侦察元数据

| 项目 | 值 |
|------|-----|
| 侦察范围 | mms/ 全量 + mysql_multi_source_main.py 全量 + 测试文件 + 文档 |
| 代码行数 | Python 4917 行（主文件 3785 + mms 1132） |
| 测试覆盖 | mms/ 85%（236 passed, 0 failed） |
| CI 状态 | GitHub Actions 配置正常 |
| 信息截至 | 2026-05-05 |
