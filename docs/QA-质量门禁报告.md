# 阶段三十二 质量门禁报告

> 审查时间：2026-05-05
> 审查范围：ITER-032-01 ~ ITER-032-09，共 9 项变更，涉及 19 个文件，+2601/-261 行
> 测试结果：236 passed, 0 failed (0.83s)
> 模块覆盖率：mms/ 总计 85%

---

## 总体结论：有条件通过

阶段三十二的安全加固方向正确、兼容性适配基本完整、测试奠基质量较高。但存在 **2 项中风险** 和 **4 项低风险** 问题需要跟踪处理，不应阻塞合并但必须在下一轮迭代中修复。

---

## 一、安全审查

### 1.1 GTID 校验正则 (ITER-032-01) — 通过

| 检查项 | 结果 | 说明 |
|--------|------|------|
| 正则锚定 `^...$` | 通过 | 防止前后拼接注入 |
| UUID 段格式 | 通过 | `[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}` 严格匹配标准 UUID |
| interval 段格式 | 通过 | `\d+(-\d+)*(:\d+(-\d+)*)*` 覆盖单值、范围、多段 |
| 多 UUID 逗号分隔 | 通过 | `(?:,{unit})*` 正确匹配 |
| SQL 注入测试 | 通过 | 测试覆盖单引号、OR 1=1、UNION SELECT |
| 空值/None 处理 | 通过 | `str(value or "").strip()` 安全降级 |
| 正面测试覆盖 | 通过 | 含单事务、多段范围、前导/尾随空格、逗号等 |

**结论**：正则设计合理，测试充分。

### 1.2 XOR Fallback 移除 (ITER-032-02) — 通过（附说明）

| 检查项 | 结果 | 说明 |
|--------|------|------|
| 写入侧 XOR 移除 | 通过 | `_crypto_encrypt` 不再包含 XOR 分支，无 Fernet 时抛 RuntimeError |
| 旧密文读取保留 | 通过 | `_crypto_decrypt` 仍处理 `xor:` 前缀，保证升级平滑 |
| 测试覆盖 | 通过 | `test_xor_backward_compat` 验证旧 XOR 密文可正常解密 |
| Fernet 缺失时行为 | 通过 | 加密抛 RuntimeError，解密返回空字符串 |

**结论**：写入侧完全移除、读取侧向后兼容，设计正确。

### 1.3 配置单密码加密 (ITER-032-03) — 通过

| 检查项 | 结果 | 说明 |
|--------|------|------|
| sources 密码加密 | 通过 | `_migrate_config` 检测非 `enc:v1:` 前缀的明文密码并加密 |
| master_profiles 密码加密 | 通过 | 同时加密 payload 中的密码 |
| 签名同步更新 | 通过 | 密码加密后重新计算 HMAC 签名，避免导入时验签失败 |
| _save_config 加密 | 通过 | 保存时也检查并加密明文密码 |
| 测试覆盖 | 通过 | 含明文加密、已加密跳过、签名更新等测试 |

**结论**：迁移和保存两条路径均覆盖，签名同步机制正确。

### 1.4 SSH 密码泄露 (ITER-032-04) — 通过

| 检查项 | 结果 | 说明 |
|--------|------|------|
| `--password` 参数移除 | 通过 | 远程命令模板不再包含 `--password={pwd_q}` |
| MYSQL_PWD 环境变量保留 | 通过 | 通过 `MYSQL_PWD={pwd_q}` env 传递密码 |
| grep 全量扫描 | 通过 | 主文件 SSH 命令区域无残留 `--password` |

**结论**：密码仅通过环境变量传递，不出现在进程列表中。

---

## 二、兼容性审查

### 2.1 MySQL 8.0.23+ 复制语法适配 (ITER-032-05) — 有条件通过

| 检查项 | 结果 | 说明 |
|--------|------|------|
| SHOW_STATUS 调用点 | 通过 | `_get_source_status` 改用 `_replication_sql("SHOW_STATUS")` |
| STOP 调用点 | 通过 | `stop_channel` 等处改用 `_replication_sql("STOP")` |
| CHANGE_MASTER 调用点 | 通过 | `auto_start_replication` 等处改用 `_replication_sql("CHANGE_MASTER")` |
| START 调用点 | 通过 | 改用 `_replication_sql("START")` |
| **`_all_slave_status` 方法** | **未通过** | `mms/dashboard_service.py:95` 仍硬编码 `"SHOW SLAVE STATUS"` 和 `"SHOW REPLICA STATUS"`，未走 `_replication_sql` |

**问题 [中风险]**：`dashboard_service.py` 的 `_all_slave_status()` 方法（第 91-109 行）仍然使用硬编码的 `"SHOW SLAVE STATUS"` 作为首选拼接查询，通过 try/except 回退到 `"SHOW REPLICA STATUS"`。虽然功能上能工作，但：
1. 违反了"所有复制 SQL 统一走适配函数"的设计意图
2. 在 MySQL 9.0+ 中 `SHOW SLAVE STATUS` 可能被完全移除，当前回退机制依赖异常，不够健壮
3. 该方法未按 channel 过滤，无法直接复用 `_replication_sql`（它需要 channel 参数），需要扩展 `replication_sql` 函数支持无 channel 的全局查询，或在 mixin 中添加 `_replication_sql_all()` 辅助方法

### 2.2 my.cnf include 防覆盖 (ITER-032-06) — 通过

| 检查项 | 结果 | 说明 |
|--------|------|------|
| 独立配置文件 | 通过 | 复制配置写入 `multi_source.cnf`，通过 `!include` 引入 |
| 目录探测 | 通过 | 优先 `/etc/my.cnf.d`、`/etc/mysql/conf.d`，fallback 到 my.cnf 同目录 |
| 配置迁移 | 通过 | 首次执行时从 my.cnf 移除已迁移的键 |
| 备份机制 | 通过 | 首次修改前备份 my.cnf |

---

## 三、测试审查

### 3.1 测试整体情况

| 指标 | 值 |
|------|------|
| 总测试数 | 236 |
| 通过率 | 100% |
| 执行时间 | 0.83s |
| mms/ 模块覆盖率 | 85% |

### 3.2 各模块覆盖率

| 模块 | 覆盖率 | 评价 |
|------|--------|------|
| validators.py | 100% | 充分 |
| logging_audit.py | 100% | 充分 |
| replication_syntax.py | 100% | 充分 |
| config_store.py | 92% | 良好，缺失行：签名更新异常处理、保存失败 |
| crypto.py | 90% | 良好，缺失行：Fernet 库缺失警告、key 生成 fallback |
| handshake_service.py | 91% | 良好 |
| dashboard_service.py | 89% | 良好 |
| **diagnose_service.py** | **34%** | **不足** |

**问题 [中风险]**：`diagnose_service.py` 覆盖率仅 34%，`diagnose_source`、`wizard_diagnose_all`、`wizard_quick_fix` 三个核心方法均未覆盖。测试文件仅测试了 `_classify_error` 和 `_classify_connectivity_error` 两个纯函数，缺少集成测试。

### 3.3 测试边界条件

| 检查项 | 结果 |
|--------|------|
| GTID 注入：空字符串 | 通过 |
| GTID 注入：None | 通过 |
| GTID 注入：SQL 注入变体 | 通过 |
| 密码加密往返（含 Unicode、长密码） | 通过 |
| XOR 旧密文向后兼容 | 通过 |
| 并发配置更新（4 线程 x 5 轮） | 通过 |
| 复制语法新旧版本全覆盖 | 通过 |
| Profile 过期/篡改/重复导入 | 通过 |

**缺失的边界条件 [低风险]**：
- GTID 正则未测试包含换行符 (`\n`) 的输入
- 未测试 GTID 值中包含 null 字节 (`\x00`) 的情况
- `_crypto_decrypt` 未测试损坏的 XOR 密文（base64 解码失败场景）

---

## 四、代码质量

### 4.1 裸 except 改造 (ITER-032-08) — 有条件通过

本次提交将主文件中 Top 10 的裸 `except Exception:` 改为具体异常类型或带日志的 `except Exception as e`。但：

**问题 [低风险]**：主文件 `mysql_multi_source_main.py` 中仍残留约 **70 处** `except Exception:` 未分类捕获。本次仅改造了约 15 处。`diagnose_service.py` 中 `wizard_diagnose_all` 方法第 125 行也有裸 except。

### 4.2 CI 配置 (ITER-032-09) — 通过

| 检查项 | 结果 |
|--------|------|
| Python 版本矩阵 | 3.8, 3.10, 3.12 |
| Lint (flake8) | 配置了语法错误和未定义名称检查 |
| 测试 + 覆盖率 | pytest + pytest-cov |
| 依赖安装 | cryptography, pymysql |

---

## 五、向后兼容性

| 检查项 | 结果 | 说明 |
|--------|------|------|
| 配置版本升级 | 通过 | `version` 从 `"1"` 自动迁移到 `"2.0.0"` |
| 旧密码自动加密 | 通过 | `_migrate_config` 和 `_save_config` 双路径加密 |
| XOR 旧密文可读 | 通过 | `_crypto_decrypt` 保留 `xor:` 分支 |
| 无需人工干预 | 通过 | 所有迁移在 `_load_config` 时自动完成 |
| 版本降级安全 | **注意** | 旧版本代码读取 `version: "2.0.0"` 配置时会走 `_default_config` 忽略现有数据，但这是旧版本的已有行为，不构成回归 |

---

## 六、建议改进项

### 必须修复（下一轮迭代）

| 编号 | 优先级 | 描述 | 涉及文件 |
|------|--------|------|----------|
| FIX-01 | 中 | `_all_slave_status` 应走 `_replication_sql` 适配路径，扩展为支持无 channel 的全局查询 | `mms/dashboard_service.py` |
| FIX-02 | 中 | `diagnose_service.py` 补充 `diagnose_source`、`wizard_diagnose_all`、`wizard_quick_fix` 的集成测试 | `tests/test_diagnose_service.py` |

### 建议修复（择机处理）

| 编号 | 优先级 | 描述 | 涉及文件 |
|------|--------|------|----------|
| SUG-01 | 低 | 主文件剩余 ~70 处裸 `except Exception:` 应继续分类改造 | `mysql_multi_source_main.py` |
| SUG-02 | 低 | GTID 正则补充换行符和 null 字节的防御性测试 | `tests/test_validators.py` |
| SUG-03 | 低 | `_crypto_key` 中 `os.chmod` 失败时静默吞异常，应至少记录 debug 日志 | `mms/crypto.py` |
| SUG-04 | 低 | CHANGELOG.md 和 README.md 未在本轮提交中更新 | 项目根目录 |

---

## 附录：审查清单

- [x] 所有复制 SQL 是否统一走适配函数（除 `_all_slave_status`）
- [x] SSH 命令中无 `--password` 参数
- [x] XOR fallback 写入侧已移除、读取侧保留
- [x] 配置迁移自动加密明文密码并更新签名
- [x] GTID 校验正则锚定且覆盖常见注入向量
- [x] 测试全部通过 (236/236)
- [x] mms/ 模块总体覆盖率 85%
- [ ] `diagnose_service.py` 覆盖率不足 (34%)
- [ ] `_all_slave_status` 未走适配函数
- [ ] CHANGELOG.md 未更新
