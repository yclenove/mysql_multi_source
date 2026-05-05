# ITER-032 — mysql_multi_source 迭代计划 v2.1.0

> 迭代主题：安全加固 + 测试奠基 | 版本目标：v2.1.0 | 总工时：10d 开发 + 2d 缓冲 = 12d

---

## 迭代目标

> 消除 3 项严重安全漏洞（SQL 注入、明文密码、弱加密），适配 MySQL 8.0.23+ 新语法，为 mms/ 模块建立首批单元测试，为后续持续集成奠基。

---

## 文档健康检查

- [x] PRD：暂无正式 PRD，功能需求由侦察报告 + 反馈分析覆盖
- [x] 反馈分析：`docs/FEEDBACK-反馈分析报告.md` 存在（18 条有效反馈）
- [x] 侦察报告：`docs/SCOUT-迭代侦察报告.md` 存在
- [x] 理解项目现状：v2.0.0，31 阶段完成，0% 测试覆盖

---

## 最终任务清单

### Phase 1：安全修复（并行执行，无依赖）

| ID | 名称 | 来源 | 优先级 | 预估 | 风险 | 范围 |
|----|------|------|--------|------|------|------|
| ITER-032-01 | GTID purged SQL 注入校验 | FB-004 / TD | P0 | 0.5d | 低 | 单函数 |
| ITER-032-02 | 移除 XOR fallback，强制 cryptography | FB-002 / TD-5 | P0 | 0.5d | 中 | 单模块 mms/crypto.py |
| ITER-032-03 | 配置单密码加密 | FB-007 | P0 | 1d | 中 | 跨模块（handshake + config_store） |
| ITER-032-04 | 物理初始化密码改 stdin pipe | TD-3 | P0 | 0.5d | 低 | 单函数区域（L748-770） |

### Phase 2：兼容性修复（依赖 Phase 1 完成后并行）

| ID | 名称 | 来源 | 优先级 | 预估 | 风险 | 范围 |
|----|------|------|--------|------|------|------|
| ITER-032-05 | MySQL 8.0.23+ 语法适配 | FB-008 / FB-017 | P1 | 2d | 中 | 主文件 10+ 处 SQL |
| ITER-032-06 | my.cnf include 防覆盖 | FB-011 | P1 | 1d | 中 | 主文件配置写入逻辑 |

### Phase 3：测试奠基（依赖 Phase 1 完成）

| ID | 名称 | 来源 | 优先级 | 预估 | 风险 | 范围 |
|----|------|------|--------|------|------|------|
| ITER-032-07 | mms/ 模块 pytest 测试 | FB-015 / TD-1 | P1 | 3d | 低 | 新增 tests/ 目录 |

### Phase 4：工程化改进（建议做，可裁剪）

| ID | 名称 | 来源 | 优先级 | 预估 | 风险 | 范围 |
|----|------|------|--------|------|------|------|
| ITER-032-08 | 裸 except 分类捕获 Top 10 | FB-012 | P2 | 1d | 低 | 主文件 10 处 |
| ITER-032-09 | GitHub Actions CI 流水线 | TD-7 | P2 | 0.5d | 低 | 新增 .github/workflows/ |

---

## 依赖关系与执行顺序

```
Phase 1（安全修复，可并行）
  ├─ ITER-032-01 GTID 注入校验
  ├─ ITER-032-02 移除 XOR fallback
  ├─ ITER-032-03 配置单密码加密  ──┐
  └─ ITER-032-04 密码 stdin pipe   │
                                    │
Phase 2（兼容性，Phase 1 后并行）   │
  ├─ ITER-032-05 MySQL 8.0.23+ 语法 │  （03 完成后才能测配置单解密流程）
  └─ ITER-032-06 my.cnf include     │
                                    │
Phase 3（测试，Phase 1 后可开始）    │
  └─ ITER-032-07 pytest 测试 ◄──────┘  （测试需覆盖 Phase 1 的变更）
                                    │
Phase 4（工程化，可与 Phase 3 并行）
  ├─ ITER-032-08 裸 except 改造
  └─ ITER-032-09 CI 流水线          （依赖 07 完成才能跑 test 步骤）
```

**关键路径**：01/02/04 并行 -> 03 -> 07 -> 09

---

## 详细验收标准

### ITER-032-01：GTID purged SQL 注入校验

**Given** 一个包含非法字符的 captured_gtid 值
**When** 调用 `_auto_start_channel_after_bootstrap`
**Then** 拒绝执行并记录错误日志，不发送 SQL 到 MySQL

验收：
- [ ] `_validate_gtid_set()` 函数存在，正则 `^\d+:\d+(-\d+:\d+)*(,\d+:\d+(-\d+:\d+)*)*$`
- [ ] 非法 GTID 值（含单引号/空格/SQL关键字）被拒绝
- [ ] 合法 GTID 值正常通过
- [ ] 单元测试覆盖合法/非法各 3 组

### ITER-032-02：移除 XOR fallback

**Given** 系统中 cryptography 库不可用
**When** 调用 `_crypto_encrypt`
**Then** 抛出明确异常（而非静默降级到 XOR）

验收：
- [ ] `_HAS_FERNET = False` 时，`_crypto_encrypt` 抛出 `RuntimeError`
- [ ] `_crypto_decrypt` 仍能解密已有的 `xor:` 前缀密文（向后兼容读取）
- [ ] 启动时检测 cryptography，缺失则日志 WARNING 并提示安装
- [ ] 单元测试覆盖：Fernet 正常、Fernet 缺失报错、旧 xor 密文可解密

### ITER-032-03：配置单密码加密

**Given** 一份包含 repl_password 的配置单
**When** 导出配置单（`master_export_signed_profile`）
**Then** payload 中 repl_password 使用 Fernet 加密，导入时自动解密

验收：
- [ ] `master_export_signed_profile` 输出的 payload.repl_password 为 `__ENCRYPTED__:` 前缀
- [ ] `replica_verify_profile` 导入时自动解密
- [ ] 已有配置单在 `_migrate_config` 中自动加密（向后兼容）
- [ ] 单元测试覆盖：导出加密、导入解密、旧明文迁移

### ITER-032-04：物理初始化密码 stdin pipe

**Given** 物理初始化任务执行 xtrabackup
**When** SSH 远程执行备份命令
**Then** 密码通过 stdin pipe 传入，不出现在进程列表

验收：
- [ ] 远程命令中移除 `--password=...` 参数
- [ ] 改用 `echo 'password' | mysql --defaults-extra-file=/dev/stdin` 或 `MYSQL_PWD` 环境变量（仅 env，不带 --password）
- [ ] 进程列表中不可见明文密码
- [ ] 功能回归：物理初始化流程正常完成

### ITER-032-05：MySQL 8.0.23+ 语法适配

**Given** MySQL 版本 >= 8.0.23
**When** 执行复制相关 SQL
**Then** 使用新语法（CHANGE REPLICATION SOURCE TO / START REPLICA / STOP REPLICA）

验收：
- [ ] `_mysql_version()` 辅助函数存在，返回 (major, minor, patch) 元组
- [ ] `_replication_sql()` 根据版本返回正确语法
- [ ] 旧语法（CHANGE MASTER TO）在 < 8.0.23 仍正常工作
- [ ] 新语法在 >= 8.0.23 正常工作
- [ ] 所有 10+ 处复制 SQL 调用统一走适配函数
- [ ] 单元测试覆盖版本判断逻辑

### ITER-032-06：my.cnf include 防覆盖

**Given** 宝塔面板操作 MySQL 配置
**When** 插件写入多源复制配置
**Then** 配置写入独立文件，通过 include 指令引入

验收：
- [ ] 多源复制配置写入 `/etc/my.cnf.d/multi_source.cnf`（或等效路径）
- [ ] 主 `my.cnf` 中存在 `!include` 或 `!includedir` 指令
- [ ] 配置写入前检测 include 指令是否存在，不存在则添加
- [ ] 回归：MySQL 重启后多源复制配置生效

### ITER-032-07：mms/ 模块 pytest 测试

**Given** mms/ 下的 4 个核心模块
**When** 执行 `pytest tests/`
**Then** 全部通过，核心逻辑覆盖率 > 80%

验收：
- [ ] `tests/` 目录结构：`test_validators.py` / `test_crypto.py` / `test_config_store.py` / `test_handshake_service.py`
- [ ] validators 测试：channel_name / source_id / mysql_scope_name / privileges_text 各 5+ 用例
- [ ] crypto 测试：加密解密往返、空值处理、前缀检测、XOR 兼容、Fernet 缺失
- [ ] config_store 测试：默认配置、加载保存、迁移逻辑、并发安全
- [ ] handshake_service 测试：配置单导出/导入/签名验证/过期
- [ ] `pytest --cov=mms --cov-report=term-missing` 覆盖率 > 80%
- [ ] 所有测试可在无 MySQL 实例环境下运行（mock public 模块）

### ITER-032-08：裸 except 分类捕获 Top 10

**Given** 主文件中 30+ 处 `except Exception`
**When** 执行改造
**Then** Top 10 高频路径改为分类捕获 + 结构化日志

验收：
- [ ] 选取标准：错误信息被吞没的、涉及安全操作的、涉及文件 I/O 的
- [ ] 改为 `except (OSError, json.JSONDecodeError, ...) as e:` 并记录 `logger.warning/error`
- [ ] 不改变原有业务逻辑行为
- [ ] 单元测试不受影响（全部通过）

### ITER-032-09：GitHub Actions CI 流水线

**Given** 代码推送到 main 分支或 PR
**When** GitHub Actions 触发
**Then** 执行 lint + test，结果报告到 PR

验收：
- [ ] `.github/workflows/ci.yml` 存在
- [ ] 包含 lint 步骤（flake8 或 ruff）
- [ ] 包含 test 步骤（pytest）
- [ ] Python 3.8 / 3.10 / 3.12 矩阵测试
- [ ] PR 页面可见绿色/红色状态

---

## 影响分析

| 维度 | 影响 | 风险等级 |
|------|------|----------|
| 代码 | Phase 1 修改 mms/ 3 个文件 + 主文件 1 处；Phase 2 修改主文件 10+ 处 | 中 |
| 数据 | 无 schema 变更；已有配置单通过 `_migrate_config` 自动迁移 | 低 |
| API | 无新增/删除端点；配置单导出格式向后兼容 | 低 |
| 测试 | 新增 tests/ 目录，不影响现有代码 | 低 |
| 文档 | 需更新 CHANGELOG | 低 |

---

## 风险清单

| 风险 | 概率 | 影响 | 缓解方案 |
|------|------|------|----------|
| 移除 XOR fallback 导致无法解密旧数据 | 中 | 高 | 保留 `_crypto_decrypt` 对 `xor:` 前缀的读取能力，仅禁止新写入 |
| MySQL 版本检测在容器/宝塔环境中失败 | 低 | 中 | 提供手动覆盖配置项 `mysql_version_override` |
| my.cnf include 路径在不同发行版不一致 | 中 | 中 | 检测多个候选路径，支持配置覆盖 |
| pytest mock public 模块工作量超预期 | 低 | 低 | 先写 thin adapter，后续迭代深化 |

---

## 回滚方案

### ITER-032-01 ~ 04（安全修复）

**触发条件**：加密/解密流程异常、GTID 校验误拦截合法值

**回滚步骤**：
1. `git revert <commit-hash>` 回退对应提交
2. 重启插件服务
3. 验证：创建 channel 流程正常、配置单导出导入正常

**回滚时间**：5 分钟（纯代码回滚）

### ITER-032-05（MySQL 语法适配）

**触发条件**：旧版 MySQL 复制命令执行失败

**回滚步骤**：
1. `git revert <commit-hash>`
2. 重启插件
3. 验证：STOP/START SLAVE 正常工作

**回滚时间**：5 分钟

### ITER-032-06（my.cnf include）

**触发条件**：MySQL 启动失败、配置未生效

**回滚步骤**：
1. 删除 `/etc/my.cnf.d/multi_source.cnf`
2. 从备份恢复原始 `my.cnf`（插件自动备份原配置）
3. 重启 MySQL
4. `git revert <commit-hash>`

**回滚时间**：10 分钟（含 MySQL 重启）

### ITER-032-07（测试代码）

**触发条件**：不适用（测试代码不影响生产）

**回滚步骤**：`rm -rf tests/` 或 `git revert`

**回滚时间**：1 分钟

---

## 迭代容错

| 场景 | 处理方式 |
|------|----------|
| ITER-032-05（2d）超期 | 降级为仅适配最常用的 3 处 SQL，其余排入下迭代 |
| ITER-032-07（3d）超期 | 仅完成 validators + crypto 测试（覆盖安全模块），config_store/handshake 排入下迭代 |
| ITER-032-03 跨模块变更引发回归 | 回滚 03，保留 01/02/04 的安全修复 |
| Phase 4 整体延期 | 全部移入下迭代，不影响核心安全目标 |

---

## 版本策略

- 当前版本：v2.0.0
- 目标版本：**v2.1.0**（minor 升级：安全加固 + 兼容性改进 + 测试奠基）
- 不涉及 breaking change（配置单格式向后兼容，旧 xor 密文仍可读取）

---

## 发布检查清单

- [ ] Phase 1 全部 4 项安全修复完成并通过测试
- [ ] Phase 2 兼容性修复完成并通过测试
- [ ] Phase 3 pytest 测试全部通过，覆盖率 > 80%
- [ ] 配置单加密向后兼容：旧明文配置单可自动迁移
- [ ] XOR fallback 读取兼容：旧 xor 密文仍可解密
- [ ] MySQL 5.7 / 8.0 / 8.4 语法分支均有测试覆盖
- [ ] CHANGELOG.md 已更新
- [ ] 回滚方案已文档化（本文档）
- [ ] CI 流水线绿色（如 Phase 4 完成）

---

## 进度追踪

| 任务 | 状态 | 阻塞 | 预计完成 |
|------|------|------|----------|
| ITER-032-01 GTID 注入校验 | 待开始 | 无 | Day 1 |
| ITER-032-02 移除 XOR fallback | 待开始 | 无 | Day 1 |
| ITER-032-03 配置单密码加密 | 待开始 | 02 完成后 | Day 2 |
| ITER-032-04 密码 stdin pipe | 待开始 | 无 | Day 1 |
| ITER-032-05 MySQL 语法适配 | 待开始 | Phase 1 完成 | Day 5 |
| ITER-032-06 my.cnf include | 待开始 | Phase 1 完成 | Day 4 |
| ITER-032-07 pytest 测试 | 待开始 | 01/02/03 完成 | Day 8 |
| ITER-032-08 裸 except 改造 | 待开始 | 无 | Day 9 |
| ITER-032-09 CI 流水线 | 待开始 | 07 完成 | Day 10 |

---

## 质量门禁自检

- [x] 每项有验收标准（Given/When/Then）
- [x] 影响分析覆盖代码/数据/API/测试/文档
- [x] 每个功能有回滚方案
- [x] 回滚方案有触发条件和验证步骤
- [x] 版本号符合语义化规范（v2.0.0 -> v2.1.0）
- [x] 迭代容量不超过可用开发人天（10d 开发 + 2d 缓冲 = 12d）
