# ARCH-变更影响分析 -- 阶段三十二 (v2.1.0)

> 分析人：architect | 日期：2026-05-05 | 基于 PRD-阶段三十二 + ITERATION-PLAN-阶段三十二

---

## 变更总览

| # | 变更 | Phase | 风险 | 涉及文件数 |
|---|------|-------|------|-----------|
| 1 | GTID 注入校验 | 1 | 低 | 2 |
| 2 | 移除 XOR fallback | 1 | 中 | 1 |
| 3 | 配置单密码加密 | 1 | 中 | 2 |
| 4 | 密码 stdin pipe | 1 | 低 | 1 |
| 5 | MySQL 8.0.23+ 语法适配 | 2 | 中 | 2 (含 1 个新增) |
| 6 | my.cnf include 防覆盖 | 2 | 中 | 1 |
| 7 | pytest 测试 | 3 | 低 | 5 (全部新增) |
| 8 | 裸 except 改造 Top 10 | 4 | 低 | 1 |
| 9 | GitHub Actions CI | 4 | 低 | 1 (新增) |

---

## 变更 1：GTID 注入校验 (ITER-032-01)

### 影响文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mysql_multi_source_main.py` | 修改 | 在 L2520 `_exec_sql("SET @@GLOBAL.gtid_purged = '{}'".format(...))` 前增加正则校验 |
| `mms/validators.py` | 修改 | 新增 `_validate_gtid_set()` 方法 |

### 调用链分析

```
run_bootstrap_task (L2785)
  -> _run_logical_bootstrap / _run_physical_bootstrap
    -> _auto_start_channel_after_bootstrap (L2472)
      -> [新增] _validate_gtid_set(captured_gtid)  -- 校验点
      -> _exec_sql("SET @@GLOBAL.gtid_purged = '{}'".format(...))  -- 被保护的目标
```

**上游**：`captured_gtid` 来源于 `task.get("master_gtid_at_dump")`，由 `_run_logical_bootstrap` 在 mysqldump 前通过 `SELECT @@GLOBAL.gtid_executed` 捕获写入任务对象。

**下游**：校验失败时应 raise 异常，被 L2814 的 `except Exception` 捕获后写入 `channel_start_error`，不影响任务最终状态标记为 `done`。

### 向后兼容性

- 无 breaking change。合法 GTID 值的格式不变，校验通过率 100%。
- 仅拦截恶意/异常输入。

### 数据迁移

- 不需要。校验逻辑在运行时执行，不修改存储数据。

### 集成风险

- 与变更 5（语法适配）无冲突：两者操作的 SQL 语句不同。
- 与变更 8（裸 except）有微弱关联：L2508/L2517/L2521 的 `except Exception` 如被改造为分类捕获，需确保新抛出的 `ValueError`/`RuntimeError` 被正确捕获。

---

## 变更 2：移除 XOR fallback (ITER-032-02)

### 影响文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mms/crypto.py` | 修改 | `_crypto_encrypt` 删除 L51-54 的 XOR 分支；`_crypto_decrypt` 保留 `xor:` 前缀读取 |

### 调用链分析

```
_crypto_encrypt(plaintext)                        -- 被以下方法调用：
  -> _save_config (config_store.py L93-96)         -- 源密码入库时
  -> _migrate_config (config_store.py L43-55)      -- 旧明文迁移
  -> master_export_signed_profile (handshake L23)   -- 配置单导出（变更 3 改造后）
  -> replica_import_profile (handshake L97)         -- 配置单导入

_crypto_decrypt(value)                            -- 被以下方法调用：
  -> _decrypted_password (main.py L226-230)         -- 启动通道/自动启动时解密密码
  -> replica_import_profile (handshake L89-90)      -- 导入配置单时解密
```

**关键约束**：`_crypto_decrypt` 必须保留对 `xor:` 前缀的读取能力。已有配置文件中的 `enc:v1:xor:...` 密文在升级后仍可正常解密。

### 向后兼容性

- **写入侧 breaking**：`_crypto_encrypt` 不再降级到 XOR，`_HAS_FERNET=False` 时直接抛 `RuntimeError`。
- **读取侧兼容**：`_crypto_decrypt` 保留 `xor:` 分支，旧密文可正常解密。
- **运行时影响**：如果目标环境未安装 `cryptography` 库，所有密码写入操作将失败。需在启动时检测并输出明确提示。

### 数据迁移

- 不需要。现有 `enc:v1:xor:...` 密文保持可读。
- 新写入一律使用 Fernet。

### 集成风险

- 与变更 3 强耦合：变更 3 的配置单加密依赖 `_crypto_encrypt`，必须在变更 2 完成后执行。
- 如果环境中无 `cryptography` 库，变更 2 + 3 组合会导致配置单导出/导入全部失败。**建议启动时做依赖检测并拒绝启动**。

---

## 变更 3：配置单密码加密 (ITER-032-03)

### 影响文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mms/handshake_service.py` | 修改 | `master_export_signed_profile` 中 `repl_password` 字段加密；`replica_import_profile` 自动解密 |
| `mms/config_store.py` | 修改 | `_migrate_config` 中对 `master_profiles[].payload.repl_password` 的迁移加密（已有逻辑，需确认覆盖） |

### 调用链分析

```
master_export_signed_profile (L11)
  -> payload["repl_password"] = self._crypto_encrypt(raw_pwd)  -- [修改点]
  -> _profile_sign(payload)                                     -- 签名覆盖加密后的值
  -> base64 编码输出

replica_verify_profile (L48)
  -> base64 解码 -> payload 提取
  -> [无变化] 签名校验

replica_import_profile (L76)
  -> replica_verify_profile(get)                                -- 验签
  -> raw_pwd = payload.get("repl_password")
  -> [新增] if raw_pwd.startswith(CRYPTO_PREFIX): raw_pwd = self._crypto_decrypt(raw_pwd)
  -> self._crypto_encrypt(raw_pwd)                              -- 重新加密入库
```

**签名影响**：`_profile_sign` 对整个 payload 做 HMAC。加密后的 `repl_password` 值与明文不同，意味着：
- 新导出的配置单签名基于加密值
- 旧导出的配置单签名基于明文值
- `replica_import_profile` 需先解密再验签，或先验签再解密——当前代码是先验签（L68），后解密（L89-90）

**关键问题**：旧配置单中 `repl_password` 为明文，签名也基于明文。导入时 `replica_import_profile` 的 L89 判断 `raw_pwd.startswith(self.CRYPTO_PREFIX)` 为 False，直接走 `self._crypto_encrypt(raw_pwd)` 加密入库——这是正确的向后兼容路径。

### 向后兼容性

- **导出端**：新导出的配置单中 `repl_password` 为 `enc:v1:...` 密文。
- **导入端**：
  - 新配置单（加密）：先验签通过 -> 检测到 `CRYPTO_PREFIX` -> 解密 -> 重新加密入库。
  - 旧配置单（明文）：先验签通过 -> 不以 `CRYPTO_PREFIX` 开头 -> 直接加密入库。
- **无 breaking change**。

### 数据迁移

- `_migrate_config` 已有逻辑（config_store.py L49-55）处理 `master_profiles[].payload.repl_password` 的迁移加密。
- 需确认：如果旧配置单 payload 中密码为明文，迁移时会加密，但签名仍为旧值。后续导入该配置单时验签会失败。
- **建议**：迁移时不改变已存储配置单的 payload（只改 sources 中的），或迁移时同时更新签名。

### 集成风险

- 强依赖变更 2（XOR 移除）：必须先完成变更 2，确保 `_crypto_encrypt` 不会静默降级。
- `_migrate_config` 中已有对 `master_profiles` 的迁移逻辑，与本次修改高度重叠，需确认不会重复加密（`CRYPTO_PREFIX` 前缀判断已防重复）。

---

## 变更 4：密码 stdin pipe (ITER-032-04)

### 影响文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mysql_multi_source_main.py` | 修改 | L757-768 的 `remote_cmd` 构造，移除 `--password=...` |

### 调用链分析

```
_run_physical_bootstrap (L700 区域)
  -> remote_cmd 构造 (L757-768)                -- [修改点]
  -> SSH pipeline 执行 (L769-779)
    -> _run_shell(["bash", "-lc", ...])         -- 实际执行
```

**当前实现**（L758-759）：
```python
"MYSQL_PWD={pwd_q} {tool} --backup --stream=xbstream "
"--user={user_q} --password={pwd_q} --host=127.0.0.1 --port={port} "
```

**目标实现**：移除 `--password={pwd_q}`，仅保留 `MYSQL_PWD` 环境变量。或改用 `--defaults-extra-file` 方式。

### 向后兼容性

- 无 breaking change。功能行为不变，仅传递密码的方式改变。
- 需验证：xtrabackup 是否支持仅通过 `MYSQL_PWD` 环境变量获取密码（已知支持）。

### 数据迁移

- 不需要。

### 集成风险

- 独立变更，与其他 8 项无冲突。
- 风险点：部分精简环境中 `MYSQL_PWD` 可能被 shell 清除（迭代计划已提及此问题）。需做回归测试。

---

## 变更 5：MySQL 8.0.23+ 语法适配 (ITER-032-05)

### 影响文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mms/replication_syntax.py` | **新增** | 封装版本检测 + 语法选择逻辑 |
| `mysql_multi_source_main.py` | 修改 | 10+ 处复制 SQL 调用改为通过适配函数 |

### 调用链分析 — 需改造的 SQL 调用点

| 行号 | 方法 | 当前 SQL | 改造方式 |
|------|------|---------|---------|
| L182 | `_get_source_status` | `SHOW SLAVE STATUS FOR CHANNEL` | `self._replication_sql("SHOW_STATUS", channel)` |
| L2458 | `delete_source` | `STOP SLAVE FOR CHANNEL` | `self._replication_sql("STOP", channel)` |
| L2532 | `_auto_start_channel_after_bootstrap` | `STOP SLAVE FOR CHANNEL` | 同上 |
| L2537 | `_auto_start_channel_after_bootstrap` | `CHANGE MASTER TO ...` | `self._replication_sql("CHANGE_MASTER", ...)` |
| L2549 | `_auto_start_channel_after_bootstrap` | `START SLAVE FOR CHANNEL` | `self._replication_sql("START", channel)` |
| L2585 | `start_channel` | `STOP SLAVE FOR CHANNEL` | 同上 |
| L2590 | `start_channel` | `CHANGE MASTER TO ...` | 同上 |
| L2602 | `start_channel` | `START SLAVE FOR CHANNEL` | 同上 |
| L2632 | `stop_channel` | `STOP SLAVE FOR CHANNEL` | 同上 |

**新增模块设计** (`mms/replication_syntax.py`)：
```python
# 核心接口
def mysql_version_tuple(conn) -> tuple:    # (major, minor, patch)
def is_new_syntax(version: tuple) -> bool: # >= 8.0.23
def replication_sql(cmd, version, **kwargs) -> str:
    # cmd: "CHANGE_MASTER" | "START" | "STOP" | "SHOW_STATUS" | "RESET"
```

### 向后兼容性

- MySQL 5.7 / 8.0.0-8.0.22：行为完全不变，使用旧语法。
- MySQL 8.0.23+：使用新语法，功能等价。
- **无 breaking change**。

### 数据迁移

- 不需要。

### 集成风险

- 与变更 1 共享 `_auto_start_channel_after_bootstrap` 方法：两者修改的行不重叠（变更 1 改 L2520 附近，变更 5 改 L2532-2549 附近），但合并时需注意上下文。
- 与变更 8（裸 except）共享多个方法：L2458/L2532/L2585/L2632 的 `except Exception` 可能被改造，需协调。
- **版本检测缓存**：建议 `_mysql_version()` 结果在实例级别缓存（每次请求检测一次即可），避免每条 SQL 前都查询版本。

---

## 变更 6：my.cnf include 防覆盖 (ITER-032-06)

### 影响文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mysql_multi_source_main.py` | 修改 | `_apply_master_mycnf_fix` (L1561-1589) 改为写入独立文件 + include 指令 |

### 调用链分析

```
master_auto_fix_apply (L1591)
  -> _apply_master_mycnf_fix (L1561)    -- [修改点]
    -> public.ReadFile(mysql_cnf_path)   -- 读取 /etc/my.cnf
    -> public.WriteFile(mysql_cnf_path)  -- 写入 /etc/my.cnf [改为写入独立文件]
```

**改造方案**：
1. 多源复制配置写入 `/etc/my.cnf.d/multi_source.cnf`（或等效路径）
2. 在主 `my.cnf` 中添加 `!include /etc/my.cnf.d/multi_source.cnf`
3. 写入前检测 include 指令是否存在

### 向后兼容性

- 首次执行时会在 `my.cnf` 中添加 `!include` 指令，这是**一次性的不可逆变更**。
- 后续更新只修改独立文件，不再触碰 `my.cnf` 主体。
- **低风险 breaking**：如果宝塔面板也在管理 `my.cnf`，添加 `!include` 指令可能触发宝塔的配置检测告警。

### 数据迁移

- 不需要数据迁移。
- 需要**文件迁移**：首次执行时将现有 `_apply_master_mycnf_fix` 写入的配置从 `my.cnf` 迁移到独立文件。

### 集成风险

- 独立变更，与其他 8 项无代码冲突。
- 运行时风险：不同 Linux 发行版的 `my.cnf.d` 路径不一致（CentOS: `/etc/my.cnf.d/`，Ubuntu: `/etc/mysql/conf.d/`）。需做路径探测。

---

## 变更 7：pytest 测试 (ITER-032-07)

### 影响文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `tests/__init__.py` | **新增** | 包标记 |
| `tests/test_validators.py` | **新增** | validators 模块测试 |
| `tests/test_crypto.py` | **新增** | crypto 模块测试 |
| `tests/test_config_store.py` | **新增** | config_store 模块测试 |
| `tests/test_handshake_service.py` | **新增** | handshake_service 模块测试 |

### 调用链分析

- 测试代码不进入生产调用链。
- 需要 mock `public` 模块（宝塔面板的 `ReadFile`/`WriteFile`/`ExecShell` 等），因为测试在无宝塔环境中运行。

### 向后兼容性

- 无影响。纯新增文件。

### 数据迁移

- 不需要。

### 集成风险

- 测试必须覆盖变更 1/2/3 的新增逻辑：GTID 校验、XOR 移除后的异常分支、配置单加密/解密。
- **执行顺序约束**：变更 7 应在变更 1/2/3 完成后编写，否则测试用例需要返工。
- `pytest --cov=mms` 覆盖率目标 >= 80%，需覆盖所有异常分支。

---

## 变更 8：裸 except 改造 Top 10 (ITER-032-08)

### 影响文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mysql_multi_source_main.py` | 修改 | 10 处 `except Exception` 改为分类捕获 |

### 选取标准与候选点

基于"错误信息被吞没 + 涉及安全操作 + 涉及文件 I/O"标准，Top 10 候选：

| 行号 | 方法 | 当前行为 | 建议改为 |
|------|------|---------|---------|
| L744 | `_run_physical_bootstrap` | `except Exception: pass` | `except (IOError, OSError) as e: logger.warning(...)` |
| L2508 | `_auto_start_channel_after_bootstrap` | `except Exception` (SELECT gtid) | `except (pymysql.Error, db_mysql.Error) as e:` |
| L2533 | `_auto_start_channel_after_bootstrap` | `except Exception: pass` (STOP SLAVE) | `except Exception as e: logger.debug(...)` |
| L2586 | `start_channel` | `except Exception: pass` (STOP SLAVE) | 同上 |
| L326 | `某方法` | `except Exception` | 待具体分析 |
| L467 | `某方法` | `except Exception` | 待具体分析 |
| L507 | `某方法` | `except Exception` | 待具体分析 |
| L686 | `某方法` | `except Exception` | 待具体分析 |
| L928 | `某方法` | `except Exception` | 待具体分析 |
| L993 | `某方法` | `except Exception` | 待具体分析 |

### 向后兼容性

- **行为不变**：分类捕获不会改变业务逻辑，只是让异常类型更精确。
- **日志增强**：原来被吞掉的异常将被记录，有利于排查问题。

### 数据迁移

- 不需要。

### 集成风险

- 与变更 1/5 共享方法（`_auto_start_channel_after_bootstrap`、`start_channel` 等）。
- **建议在变更 1/5 完成后再执行变更 8**，避免合并冲突。
- 如果变更 1 新增了 `raise ValueError(...)` 但变更 8 将对应的 `except Exception` 改为 `except (OSError, json.JSONDecodeError)`，则新异常会逃逸。需确保分类捕获覆盖新增的异常类型。

---

## 变更 9：GitHub Actions CI (ITER-032-09)

### 影响文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `.github/workflows/ci.yml` | **新增** | CI 流水线配置 |

### 向后兼容性

- 无影响。纯新增文件。

### 数据迁移

- 不需要。

### 集成风险

- 依赖变更 7（pytest 测试）完成，否则 CI 的 test 步骤无测试可跑。
- Python 版本矩阵（3.8/3.10/3.12）需确认 `cryptography` 库在各版本的兼容性。

---

## 变更间冲突矩阵

| | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|
| **1** | - | 无 | 无 | 无 | 低 | 无 | 被依赖 | 中 | 无 |
| **2** | | - | **高** | 无 | 无 | 无 | 被依赖 | 无 | 无 |
| **3** | | | - | 无 | 无 | 无 | 被依赖 | 无 | 无 |
| **4** | | | | - | 无 | 无 | 被依赖 | 无 | 无 |
| **5** | | | | | - | 无 | 被依赖 | 中 | 无 |
| **6** | | | | | | - | 无 | 无 | 无 |
| **7** | | | | | | | - | 无 | 被依赖 |
| **8** | | | | | | | | - | 无 |
| **9** | | | | | | | | | - |

**冲突说明**：
- **2-3 高**：变更 3 强依赖变更 2 的 `_crypto_encrypt` 行为。
- **1-8 中 / 5-8 中**：共享方法中的 `except` 改造可能与新增逻辑冲突。

---

## 建议执行顺序

```
Day 1:  [并行] 变更 1 (GTID 校验) + 变更 2 (XOR 移除) + 变更 4 (密码 pipe)
Day 2:  变更 3 (配置单加密)  -- 依赖变更 2 完成
Day 3-4: 变更 6 (my.cnf include)  -- 可与变更 5 并行
Day 3-5: 变更 5 (MySQL 语法适配)  -- 2d 工时
Day 6-8: 变更 7 (pytest 测试)  -- 覆盖变更 1/2/3/4/5 的新逻辑
Day 9:   变更 8 (裸 except 改造)  -- 在所有代码变更完成后执行
Day 10:  变更 9 (CI 流水线)  -- 依赖变更 7 完成
```

**关键路径**：变更 1/2/4 (并行) -> 变更 3 -> 变更 7 -> 变更 9

---

## 数据迁移汇总

| 变更 | 是否需要迁移 | 说明 |
|------|-------------|------|
| 1 GTID 校验 | 否 | 运行时校验，不改存储 |
| 2 XOR 移除 | 否 | 旧 xor 密文仍可读取 |
| 3 配置单加密 | **需关注** | `_migrate_config` 已有逻辑，但已存储配置单的签名不会自动更新 |
| 4 密码 pipe | 否 | |
| 5 语法适配 | 否 | |
| 6 my.cnf include | **文件迁移** | 首次执行需将配置从 my.cnf 迁移到独立文件 |
| 7-9 | 否 | 纯新增 |

**变更 3 的签名问题**：已存储的 `master_profiles` 中，如果 payload 的 `repl_password` 被 `_migrate_config` 加密，但签名仍是基于旧明文计算的，后续导入该配置单时验签会失败。**建议**：`_migrate_config` 中对 `master_profiles` 的迁移同时更新签名，或标记为"已迁移，跳过验签"。

---

## 回滚策略汇总

| 变更 | 回滚方式 | 回滚时间 | 数据影响 |
|------|---------|---------|---------|
| 1 | git revert | 5min | 无 |
| 2 | git revert | 5min | 无（旧 xor 密文不受影响） |
| 3 | git revert | 5min | 需手动解密已加密的配置单 |
| 4 | git revert | 5min | 无 |
| 5 | git revert | 5min | 无 |
| 6 | 删除独立文件 + 恢复 my.cnf 备份 + git revert | 10min | 需重启 MySQL |
| 7 | rm -rf tests/ | 1min | 无 |
| 8 | git revert | 5min | 无 |
| 9 | git revert | 1min | 无 |
