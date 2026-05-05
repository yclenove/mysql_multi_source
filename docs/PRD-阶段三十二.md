# PRD-阶段三十二 — 安全加固与 MySQL 兼容性

> 产品：mysql_multi_source | 版本：v2.0.0 -> v2.1.0 | 日期：2026-05-05 | 作者：product-manager

---

## 产品概述

本轮迭代聚焦**安全加固**和**MySQL 版本兼容**两条主线，补齐 v2.0.0 上线后暴露的 3 项安全漏洞和 1 项兼容性断崖，同时为 mms/ 核心模块补上首批单元测试，将测试覆盖率从 0% 提升到基础可用水平。

**一句话定位**：把安全短板补上，让 MySQL 8.0.23+ 用户能用起来，为测试体系奠基。

---

## 迭代目标

| # | 目标 | 对应反馈 | 验证方式 |
|---|------|----------|----------|
| G1 | 消除 GTID SQL 注入、配置单明文密码、XOR 弱加密 3 项安全漏洞 | FB-004, FB-007, FB-002 | 安全审计用例全部通过 |
| G2 | 支持 MySQL 8.0.23+ 新复制语法，消除语法废弃导致的功能断裂 | FB-008 | MySQL 8.0.23 / 8.4 实测通过 |
| G3 | 为 mms/ 核心模块（validators / crypto / config_store）补 pytest 单元测试，覆盖率 >= 80% | TD-1 | pytest --cov 报告 |

---

## 用户故事

| ID | 角色 | 需求 | 价值 | 优先级 |
|----|------|------|------|--------|
| US-01 | DBA | 我希望配置单中的复制密码被加密存储，以便通过聊天工具传输配置单时不会泄露密码 | 安全合规 | P0 |
| US-02 | DBA | 我希望 GTID 值在执行前被严格校验，以便防止恶意构造的数据导致 SQL 注入 | 安全合规 | P0 |
| US-03 | DBA | 我希望插件在我使用 MySQL 8.0.23+ 时自动使用新语法，以便复制功能正常工作 | 功能可用 | P0 |
| US-04 | 运维人员 | 我希望插件使用强加密保护所有密码字段，以便满足公司安全审计要求 | 安全合规 | P0 |
| US-05 | 开发者 | 我希望核心模块有单元测试覆盖，以便修改代码时能快速发现回归问题 | 工程质量 | P1 |

---

## 功能清单

### F1: 安全加固（FB-004 + FB-007 + FB-002）— P0

| 子项 | 描述 | 预估工时 |
|------|------|----------|
| F1-a GTID 注入防护 | 对 `captured_gtid` 执行正则校验 `^\d+:\d+(-\d+:\d+)*(,\d+:\d+(-\d+:\d+)*)*$`，不匹配则拒绝执行并记录审计日志 | 0.5d |
| F1-b 配置单密码加密 | `master_export_signed_profile` 中 `repl_password` 字段使用 Fernet 加密后放入 payload，导入时自动解密；base64 外壳不变 | 1d |
| F1-c 移除 XOR fallback | 删除 `mms/crypto.py` 中 XOR 回退逻辑，Fernet 不可用时直接报错并提示安装 `cryptography` 库 | 0.5d |

**验收标准：**

```
F1-a GTID 注入防护
Given 主库返回的 captured_gtid 值为 "123:456'; DROP TABLE--"
When  系统执行 _auto_start_channel_after_bootstrap
Then  操作被拒绝，审计日志记录 "GTID 格式校验失败"，不执行任何 SQL

F1-b 配置单密码加密
Given 主库导出配置单包含 repl_password
When  查看配置单 payload 的 base64 解码内容
Then  repl_password 字段为 Fernet 密文，非明文；导入端能正确解密还原

F1-c 移除 XOR fallback
Given 环境未安装 cryptography 库
When  调用 encrypt_password()
Then  抛出明确异常 "请安装 cryptography 库: pip install cryptography"，不执行 XOR 降级
```

### F2: MySQL 8.0.23+ 语法兼容（FB-008）— P0

| 描述 | 预估工时 |
|------|----------|
| 运行时检测 MySQL 版本（`SELECT VERSION()`），8.0.23+ 使用新语法：`CHANGE REPLICATION SOURCE TO` / `START REPLICA` / `STOP REPLICA` / `SHOW REPLICA STATUS`；旧版本保持原语法不变。引入 `mms/replication_syntax.py` 封装语法选择逻辑 | 2d |

**受影响命令清单：**

| 旧语法（< 8.0.23） | 新语法（>= 8.0.23） |
|---------------------|---------------------|
| `CHANGE MASTER TO` | `CHANGE REPLICATION SOURCE TO` |
| `START SLAVE` | `START REPLICA` |
| `STOP SLAVE` | `STOP REPLICA` |
| `SHOW SLAVE STATUS` | `SHOW REPLICA STATUS` |
| `RESET SLAVE` | `RESET REPLICA` |

**验收标准：**

```
Given MySQL 版本为 8.0.23
When  插件执行复制通道配置
Then  发送到 MySQL 的 SQL 语句使用 CHANGE REPLICATION SOURCE TO 语法
      且通道配置成功，SHOW REPLICA STATUS 返回正常

Given MySQL 版本为 5.7.35
When  插件执行复制通道配置
Then  发送到 MySQL 的 SQL 语句使用 CHANGE MASTER TO 语法（行为不变）

Given MySQL 版本为 8.4.0
When  插件执行复制通道配置
Then  全部使用新语法，功能正常
```

### F3: mms/ 核心模块单元测试（TD-1 部分）— P1

| 子项 | 描述 | 预估工时 |
|------|------|----------|
| F3-a validators 测试 | 覆盖 `_sql_escape`、GTID 格式校验（新增）、IP/端口校验等全部公开函数 | 0.5d |
| F3-b crypto 测试 | 覆盖 Fernet 加解密、配置单加密（新增）、异常分支 | 0.5d |
| F3-c config_store 测试 | 覆盖读写、并发安全、缺省值处理、文件锁行为 | 0.5d |

**验收标准：**

```
Given 项目根目录执行 pytest tests/ --cov=mms --cov-report=term-missing
When  测试全部运行
Then  mms/validators.py 覆盖率 >= 80%
      mms/crypto.py 覆盖率 >= 80%
      mms/config_store.py 覆盖率 >= 80%
      全部测试通过，无失败
```

---

## 不在范围内（Scope Out）

以下项目本轮**不做**，明确记录以便后续迭代排入：

| 项目 | 原因 | 建议排期 |
|------|------|----------|
| 后端主文件模块化拆分（FB-001） | 工期 3d，风险高，安全修复优先 | 阶段三十三 |
| 复制延迟告警机制（FB-009） | 新功能，安全加固优先于功能扩展 | 阶段三十三 |
| 宝塔 my.cnf 防覆盖（FB-011） | 需深入调研宝塔配置生命周期 | 阶段三十三 |
| 裸 except 分类捕获（FB-012） | 改动面广（30+ 处），需逐个验证 | 阶段三十三/三十四 |
| CI/CD 流水线（TD-7） | 依赖测试补写完成 | 阶段三十三 |
| README 重写（TD-6） | 非功能交付，可并行但不阻塞 | 阶段三十二后期 |

---

## RICE 优先级评估

| 功能 | Reach | Impact | Confidence | Effort | RICE 分 | 优先级 |
|------|-------|--------|------------|--------|---------|--------|
| F1: 安全加固 | 100% | 3 | 100% | 2d | 150 | P0 |
| F2: MySQL 8.0.23+ 兼容 | 60% | 3 | 90% | 2d | 81 | P0 |
| F3: 单元测试 | 100% | 2 | 100% | 1.5d | 133 | P1 |

---

## 非功能需求

| 维度 | 要求 | 指标 |
|------|------|------|
| 安全 | 所有密码字段不得以明文形式出现在日志、配置单、进程列表中 | 安全审计 0 高危项 |
| 兼容性 | 支持 MySQL 5.7 / 8.0 / 8.4 三个主要版本 | 版本矩阵测试全通过 |
| 可维护性 | mms/ 核心模块测试覆盖率 | >= 80% |
| 向后兼容 | 现有配置文件、配置单格式保持兼容 | 升级无需人工干预 |

---

## 产品路线图

| 阶段 | 主题 | 核心交付 | 预估总工时 |
|------|------|----------|------------|
| **三十二（本轮）** | 安全加固 + 兼容性 | SQL 注入防护、密码加密、MySQL 8.0.23+ 语法、首批测试 | **6d** |
| 三十三 | 工程化 + 告警 | 模块化拆分、延迟告警、CI/CD、裸 except 改造 | 10d |
| 三十四 | 体验优化 | my.cnf 防覆盖、结果面板持久化、接口契约统一 | 5d |
| 三十五 | 生态扩展 | MySQL 8.4 完整支持、配置迁移框架、批量操作 | 5d |

---

## 验收检查清单

- [ ] `captured_gtid` 含非法字符时执行被拒绝，审计日志有记录
- [ ] 配置单 payload 中 `repl_password` 为 Fernet 密文
- [ ] `mms/crypto.py` 中无 XOR 相关代码
- [ ] MySQL 8.0.23 环境下复制命令使用新语法
- [ ] MySQL 5.7 环境下复制命令使用旧语法（行为不变）
- [ ] `pytest tests/ --cov=mms` 覆盖率 >= 80%，全部通过
- [ ] 现有配置文件升级后无需手动修改
