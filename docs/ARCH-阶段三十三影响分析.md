# ARCH-阶段三十三影响分析 -- v2.2.0

> 分析人：architect | 日期：2026-05-05 | 基于 PRD-阶段三十三 + ITERATION-PLAN-阶段三十三

---

## 变更总览

| # | 变更 | Phase | 风险 | 涉及文件数 | 估计改动行数 |
|---|------|-------|------|-----------|-------------|
| 1 | `_all_slave_status` 走 `_replication_sql` 适配 | 1 | 中 | 3 (含 1 新增分支) | ~30 |
| 2 | 消除 3 个方法重复定义 | 1 | 中 | 1 | -150 |
| 3 | diagnose_service 测试补全 | 2 | 低 | 1 (测试) | +300 |
| 4 | 裸 except 改造 Top 20 | 2 | 低 | 1 | ~60 |
| 5 | SSH 主机密钥安全改进 | 2 | 中 | 1 | ~40 |
| 6 | 接口返回契约统一 | 3 | 中-高 | ~5 | ~50 |
| 7 | `_with_lock` 跨平台锁改进 | 4 | 中 | 1 | ~25 |

---

## 变更 1：`_all_slave_status` 走 `_replication_sql` 适配 (ITER-033-01)

### 影响文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mms/replication_syntax.py` | 修改 | `replication_sql()` 新增 `cmd="SHOW_STATUS_ALL"` 分支（不带 `FOR CHANNEL`） |
| `mms/dashboard_service.py` | 修改 | `_all_slave_status()` L91-109 删除硬编码 + try/except 回退，改为调用 `self._replication_sql("SHOW_STATUS_ALL")` |
| `tests/test_replication_syntax.py` | 修改 | 新增 `SHOW_STATUS_ALL` 用例 |

### 调用链分析

```
[入口]
  overview_metrics (L56)          -- dashboard_service.py
    -> _all_slave_status (L91)    -- dashboard_service.py（改造点）
      -> self._replication_sql("SHOW_STATUS_ALL")  -- replication_syntax.py（新增分支）
        -> self._get_mysql_version()               -- replication_syntax.py (版本缓存)
        -> replication_sql("SHOW_STATUS_ALL", ver) -- replication_syntax.py（新增分支）
      -> self._query_sql(sql)

  wizard_dashboard_snapshot (L129) -- dashboard_service.py
    -> _all_slave_status (L91)    -- 同上

  wizard_diagnose_all (L68)       -- diagnose_service.py
    -> _all_slave_status (L88)    -- 通过 self 调用，走 dashboard_service 版本

  wizard_detect_env (L3113)       -- 主文件
    -> _all_slave_status (L3120)  -- 同上
```

**上游**：所有调用 `_all_slave_status()` 的入口均通过 `self` 隐式分发到 `DashboardServiceMixin` 中的定义（当前主文件中无此方法的重复定义）。

**下游**：`_all_slave_status` 返回 `{channel_name: row}` 字典，被 `_map_status_row` 解析。改造仅变更 SQL 生成逻辑，返回结果集结构不变，下游无感知。

### 向后兼容性

- **MySQL 5.7**：`_get_mysql_version()` 返回 `(5, 7, x)`，`is_new_syntax()` 为 `False`，`replication_sql("SHOW_STATUS_ALL", ...)` 生成 `"SHOW SLAVE STATUS"`。行为与改造前完全一致。
- **MySQL 8.0.23+**：直接生成 `"SHOW REPLICA STATUS"`，不再触发无效 SQL 异常。消除每次调用时的无效 `SHOW SLAVE STATUS` + catch fallback 开销。
- **MySQL 8.4 LTS**：该版本已完全移除 `SHOW SLAVE STATUS` 语法，改造前每次调用必触发异常。改造后直接使用新语法，是本轮迭代的核心修复目标。

### 数据迁移

- 不需要。

### 集成风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| `SHOW_STATUS_ALL` 结果集过大（无 channel 过滤） | 低 | 低 | 当前 `_all_slave_status` 已是全量查询，改造不改变查询语义 |
| `_get_mysql_version()` 查询失败时 fallback 到 `(0, 0, 0)` 使用旧语法 | 低 | 低 | 与改造前 try/except 行为一致，且有 warning 日志 |
| 与变更 4（裸 except）冲突 | 低 | 低 | `_all_slave_status` 中的 `except Exception` 将被移除（改造后无需 try/except），两者互斥不冲突 |

### 重点关注：对 dashboard_service.py 的影响

改造后 `_all_slave_status` 方法体从 19 行缩减到约 5 行。核心变化：
1. **移除硬编码** `"SHOW SLAVE STATUS"` 字符串
2. **移除 try/except 双重回退** 逻辑（先试旧语法，失败再试新语法）
3. **改用版本预检测**：通过 `ReplicationSyntaxMixin._replication_sql()` 在调用前确定正确语法

`dashboard_service.py` 中的 `overview_metrics`、`wizard_dashboard_snapshot` 以及通过 `self` 间接调用的 `wizard_diagnose_all`（diagnose_service.py）和 `wizard_detect_env`（主文件）均自动受益，无需修改。

---

## 变更 2：消除 3 个方法重复定义 (ITER-033-02)

### 影响文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mysql_multi_source_main.py` | 修改 | 删除 L3625-3785 的 3 个方法定义（约 150 行） |

### 调用链分析

**MRO（方法解析顺序）当前状态：**

```python
class mysql_multi_source_main(
    ValidatorsMixin,        # mms/validators.py
    CryptoMixin,            # mms/crypto.py
    ConfigStoreMixin,       # mms/config_store.py
    LoggingAuditMixin,      # mms/logging_audit.py
    HandshakeServiceMixin,  # mms/handshake_service.py
    DashboardServiceMixin,  # mms/dashboard_service.py
    DiagnoseServiceMixin,   # mms/diagnose_service.py
    ReplicationSyntaxMixin, # mms/replication_syntax.py
):
```

Python MRO 中，类体中定义的方法优先于 mixin 继承的方法。当前主文件中这 3 个方法的定义**覆盖**了 mixin 中的同名方法，使得 mixin 版本成为死代码。

**删除后 MRO 变化：**

| 方法 | 删除前解析 | 删除后解析 |
|------|-----------|-----------|
| `wizard_dashboard_snapshot` | 主文件 L3625 | `DashboardServiceMixin` L129 |
| `wizard_diagnose_all` | 主文件 L3702 | `DiagnoseServiceMixin` L68 |
| `wizard_quick_fix` | 主文件 L3770 | `DiagnoseServiceMixin` L135 |

### 版本对比验证

逐方法对比主文件与 mixin 版本：

| 方法 | 主文件行数 | mixin 行数 | 差异 |
|------|-----------|-----------|------|
| `wizard_dashboard_snapshot` | L3625-3700 (76行) | L129-204 (76行) | **完全一致** |
| `wizard_diagnose_all` | L3702-3768 (67行) | L68-133 (66行) | **完全一致**（仅注释差异） |
| `wizard_quick_fix` | L3770-3785 (16行) | L135-150 (16行) | **完全一致** |

三个方法的主文件版本与 mixin 版本在逻辑上完全一致，不存在增量逻辑需要同步。删除主文件版本后，行为零变化。

### 向后兼容性

- 无 breaking change。外部调用者通过 `self.method_name()` 或实例方法调用，删除后由 mixin 提供同签名同逻辑的方法。
- 前端调用链不变：`wizard_dashboard_snapshot`、`wizard_diagnose_all`、`wizard_quick_fix` 均通过 BaoTa 面板的 HTTP action 路由调用，路由查找基于方法名字符串，不关心方法定义位置。

### 数据迁移

- 不需要。

### 集成风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| mixin 版本与主文件版本存在隐式差异 | 极低 | 高 | 已逐行对比确认完全一致 |
| 第三方插件/脚本通过 `inspect.getsource` 定位到主文件 | 极低 | 低 | 不属于本项目正常用法 |
| 回滚时与变更 3（测试补全）的耦合 | 中 | 中 | 回滚 02 会导致测试覆盖的是 mixin 死代码版本而非运行版本，需同步回滚 03 |

### 重点关注：MRO 变化是否影响现有调用

**结论：不影响。** 理由：
1. mixin 中的方法签名、逻辑与主文件版本完全一致
2. 方法内部调用的 `self._all_slave_status()`、`self._map_status_row()`、`self._ok()`、`self._fail()` 等辅助方法在两个位置均可通过 MRO 正确解析
3. `FakePlugin` 测试夹具（`tests/conftest.py`）的 mixin 组合与主类一致，测试已覆盖 mixin 版本路径
4. 删除后主文件减少约 150 行，符合 PRD 验收标准

---

## 变更 3：diagnose_service 测试补全 (ITER-033-03)

### 影响文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `tests/test_diagnose_service.py` | 修改 | 从 76 行扩展至约 380 行，覆盖 `diagnose_source`、`wizard_diagnose_all`、`wizard_quick_fix` |

### 调用链分析

测试将 mock 以下依赖：

```
diagnose_source(get)
  -> self._load_config()          -- mock: 返回预设配置
  -> self._find_source(data, id)  -- 走真实逻辑
  -> self._get_source_status(ch)  -- mock: 模拟正常/异常状态
  -> self.test_source_connection() -- mock: 模拟网络通/不通
  -> self.get_gtid_status()       -- mock: 模拟 GTID 开/关

wizard_diagnose_all(get)
  -> self._load_config()          -- mock
  -> self._all_slave_status()     -- mock: 模拟多 channel 状态
  -> self._map_status_row(row)    -- 走真实逻辑
  -> self._classify_error(msg)    -- 走真实逻辑
  -> self.master_health_check()   -- mock: 模拟健康检查结果

wizard_quick_fix(get)
  -> self.master_auto_fix_apply() -- mock
  -> self.recover_bootstrap_tasks() -- mock
```

### 向后兼容性

- 纯测试代码，不影响生产逻辑。

### 数据迁移

- 不需要。

### 集成风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 测试覆盖 mixin 中的死代码（如果 02 未先完成） | 中 | 中 | 迭代计划已明确 02 必须在 03 之前完成 |
| mock 不充分导致测试通过但生产环境行为不同 | 低 | 中 | 遵循现有 conftest.py 的 FakePlugin 模式，使用真实 mixin 组合 |
| 测试运行时间增加 | 低 | 低 | 纯 mock 测试，无 I/O 开销 |

### 依赖关系

- **强依赖变更 2**（ITER-033-02）：必须先删除主文件重复定义，使 mixin 版本成为运行时生效版本，测试才有意义。
- 如果 02 未完成就执行 03，测试覆盖的是 mixin 中的死代码，对生产质量无贡献。

---

## 变更 4：裸 except 改造 Top 20 (ITER-033-04)

### 影响文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mysql_multi_source_main.py` | 修改 | 约 20 处 `except Exception:` 改为分类捕获 |

### 调用链分析

改造涉及两个主要区域：

**SSH/远程操作区域（~6 处，L650-820, L2112）：**

```
_run_physical_backup (L630)
  -> _run_shell(ssh_cmd)           -- L650, L685, L697, L771, L820
    -> except Exception:           -- 改为 except (subprocess.SubprocessError, OSError) as e
       + logger.warning(...)

_run_remote_command (L2100)
  -> _run_shell(ssh_cmd)           -- L2112
    -> except Exception:           -- 改为 except (subprocess.SubprocessError, OSError) as e
```

**向导/诊断区域（~14 处，L2850-3760）：**

```
wizard_detect_env (L3113)
  -> _all_slave_status()           -- L3120, except Exception: pass
  -> check_bootstrap_tools()       -- L3128, except Exception: tools = {}
  -> _query_sql(SHOW VARIABLES)    -- L3136, except Exception: pass
  -> _query_sql(SELECT VERSION())  -- L3146, except Exception: pass

wizard_diagnose_all (L3702)
  -> master_health_check()         -- L3748, except Exception: pass
```

### 向后兼容性

- 改造不改变业务逻辑行为：异常仍被捕获，不向上抛出。
- 仅增加更精确的异常类型和结构化日志。
- 捕获范围可能略微收窄（从 `Exception` 到具体类型），但已覆盖该上下文中最可能的异常。

### 数据迁移

- 不需要。

### 集成风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 分类捕获遗漏某些异常路径 | 低 | 中 | 保留 `except Exception as e: logger.warning(...)` 作为兜底 |
| 与变更 1 冲突（`_all_slave_status` 中的 except 被移除） | 低 | 低 | 变更 1 已移除该方法中的 try/except，两者不重叠 |
| 日志量增加影响性能 | 极低 | 低 | warning 级别日志，频率不高 |

---

## 变更 5：SSH 主机密钥安全改进 (ITER-033-05)

### 影响文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mysql_multi_source_main.py` | 修改 | 6 处 `StrictHostKeyChecking=no` 改为基于配置项的动态参数 |
| 配置文件 schema | 修改 | 新增 `ssh_strict_host_key` 配置项（默认 `accept-new`） |

### 调用链分析

6 处 SSH 调用分布：

| 行号 | 所属方法 | 用途 |
|------|---------|------|
| L650 | `_run_physical_backup` | xtrabackup 远程备份 |
| L685 | `_run_physical_backup` | 远程 prepare |
| L697 | `_run_physical_backup` | 远程 copy-back |
| L771 | `_run_physical_backup` | xbstream 管道 |
| L820 | `_run_physical_backup` | 远程权限修复 |
| L2112 | `_run_remote_command` | 通用远程命令执行 |

改造模式：

```python
# 改造前
"ssh", "-o", "StrictHostKeyChecking=no", ...

# 改造后
ssh_strict = self._get_ssh_strict_mode()  # 读取配置，默认 "accept-new"
"ssh", "-o", "StrictHostKeyChecking={}".format(ssh_strict), ...
if ssh_strict == "no":
    logger.warning("SSH 主机密钥验证已禁用，存在 MITM 风险")
```

### 向后兼容性

- **新增配置项** `ssh_strict_host_key`，默认值 `accept-new`。现有配置文件无需修改即可升级。
- 用户可显式设置为 `no` 恢复旧行为。
- `accept-new` 是 OpenSSH 7.6+ 支持的选项，首次连接自动接受主机密钥，后续连接验证。兼顾安全和易用性。

### 数据迁移

- 不需要。配置项有默认值，运行时动态读取。

### 集成风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 旧版 OpenSSH（< 7.6）不支持 `accept-new` | 低 | 高 | 检测 OpenSSH 版本，< 7.6 时 fallback 到 `no` 并记录 warning |
| `accept-new` 首次连接时 known_hosts 文件权限问题 | 低 | 中 | 确保 `~/.ssh/` 目录权限为 700，known_hosts 为 600 |
| 配置项读取失败时的默认行为 | 极低 | 低 | 默认 `accept-new`，最安全的选项 |

---

## 变更 6：接口返回契约统一 (ITER-033-06)

### 影响文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mms/handshake_service.py` | 修改 | 5 处 `public.returnMsg` 改为 `self._ok/_fail` |
| `mms/dashboard_service.py` | 修改 | 8 处 `public.returnMsg` 改为 `self._ok/_fail` |
| `mms/diagnose_service.py` | 修改 | 3 处 `public.returnMsg` 改为 `self._ok/_fail` |
| `mysql_multi_source_main.py` | 修改 | 约 30+ 处 `public.returnMsg` 改为 `self._ok/_fail` |
| 前端代码 | 无需修改 | 见下方兼容性分析 |

### 调用链分析

**当前返回格式对比：**

```python
# public.returnMsg 格式
{"status": True/False, "msg": <payload>}

# _ok 格式
{"status": True, "msg": {"message": message, "code": code, ...data}}

# _fail 格式
{"status": False, "msg": {"message": str, "code": code, ...data}}
```

**关键发现：`_ok` 和 `_fail` 底层仍调用 `public.returnMsg`。**

```python
def _ok(self, data=None, message="ok", code="OK"):
    payload = data if data is not None else {}
    if isinstance(payload, dict):
        payload.setdefault("message", message)
        payload.setdefault("code", code)
    return public.returnMsg(True, payload)  # <-- 仍走 public.returnMsg

def _fail(self, message, code="ERR_GENERIC", data=None):
    payload = {"message": str(message), "code": code}
    if isinstance(data, dict):
        payload.update(data)
    return public.returnMsg(False, payload)  # <-- 仍走 public.returnMsg
```

### 向后兼容性分析

**前端兼容性：**

前端通过 `isOk(res)` 检查 `res.status === true`，通过 `extractMsg(res)` 取 `res.msg`。

- 改造前（`public.returnMsg(True, data)`）：`msg` 可以是任意类型（string、dict、list）
- 改造后（`self._ok(data)`）：`msg` 始终是 dict，且包含 `message` 和 `code` 字段

**风险点：当前部分 `public.returnMsg(True, string_value)` 调用，`msg` 是字符串。改造为 `_ok(string_value)` 后，`msg` 变为 `{"message": string_value, "code": "OK"}`，结构发生变化。**

前端代码中 `extractMsg(res)` 返回 `res.msg`，如果前端直接读取 `msg` 作为字符串（如 `get_source_logs` 返回日志内容），改造后将变为 dict，导致前端解析异常。

**需要排查的高风险调用点：**

| 方法 | 当前返回 | 改造后 | 前端影响 |
|------|---------|--------|---------|
| `get_source_logs` | `returnMsg(True, "log content")` | `_ok("log content")` | **高风险**：前端直接使用 `msg` 作为字符串 |
| `get_task_logs` | `returnMsg(True, "log content")` | `_ok("log content")` | **高风险**：同上 |
| `cancel_task` | `returnMsg(True, "任务已取消")` | `_ok("任务已取消")` | 低风险：前端可能只检查 status |
| `master_get_profile` | `returnMsg(True, profile_dict)` | `_ok(profile_dict)` | 中风险：msg 结构变化 |

### 集成风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| **前端解析 msg 为字符串时失败** | **高** | **高** | 改造前必须逐一排查前端对 msg 的使用方式；对返回纯字符串的场景保留 `public.returnMsg` 或修改前端 |
| `msg` 结构变化导致前端 `getMessage()` 返回异常 | 中 | 中 | `getMessage()` 已处理 `msg` 为 string 和 object 两种情况 |
| 主文件中 30+ 处改造遗漏 | 中 | 中 | 使用 `grep -rn "public.returnMsg"` 逐一确认 |
| 测试中 assert `result["msg"]` 的断言需更新 | 中 | 低 | 测试代码同步修改 |

### 重点关注：大范围改造的回归风险

**这是本轮迭代中风险最高的变更。** 理由：

1. **涉及面广**：mms/ 约 16 处 + 主文件约 30+ 处 = 约 50 处改造
2. **返回格式变化**：`_ok` 将 `msg` 从原始值包装为 `{message, code, ...data}` dict，破坏了前端对 `msg` 为原始类型的假设
3. **前端无变更计划**：迭代计划中未包含前端适配工作
4. **测试覆盖不完整**：主文件中的方法缺乏测试覆盖，改造后无法通过自动化测试验证

**建议：**
- Phase 3（ITER-033-06）标记为"可裁剪"是正确的。如工期紧张，应优先完成 Phase 1-2 的 P0/P1 任务。
- 如必须执行，应先改造返回 dict 的调用点（安全），再逐个处理返回字符串/列表的调用点（需同步修改前端或保留 `public.returnMsg`）。
- 对 `get_source_logs` 和 `get_task_logs` 这两个返回纯字符串的场景，建议保留 `public.returnMsg` 或改用 `_ok({"content": string})` 并同步修改前端。

---

## 变更 7：`_with_lock` 跨平台锁改进 (ITER-033-07)

### 影响文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `mysql_multi_source_main.py` | 修改 | `_with_lock` 方法 L104-124 增加 Windows fallback |

### 调用链分析

```
[所有配置读写操作]
  -> _with_lock() (L104)           -- context manager
    -> Linux: fcntl.flock(LOCK_EX) -- 现有逻辑
    -> Windows: msvcrt.locking()   -- 新增 fallback
    -> yield (临界区)
    -> unlock
```

`_with_lock` 是配置文件原子读写的核心锁机制，被 `_load_config` 和 `_save_config` 间接调用，覆盖所有配置变更操作。

### 改造方案

```python
@contextlib.contextmanager
def _with_lock(self):
    self._ensure_dirs()
    fp = None
    if _fcntl is not None:
        # Linux/macOS: fcntl.flock
        try:
            fp = open(self.config_lock_path, "a+")
            _fcntl.flock(fp.fileno(), _fcntl.LOCK_EX)
        except Exception:
            fp = None
    else:
        # Windows: msvcrt.locking fallback
        try:
            import msvcrt
            fp = open(self.config_lock_path, "a+")
            msvcrt.locking(fp.fileno(), msvcrt.LK_LOCK, 1)
        except ImportError:
            logger.warning("无可用文件锁机制（无 fcntl/msvcrt），并发写入可能损坏配置")
            fp = None
        except Exception:
            fp = None
    try:
        yield
    finally:
        if fp is not None:
            try:
                if _fcntl is not None:
                    _fcntl.flock(fp.fileno(), _fcntl.LOCK_UN)
                else:
                    import msvcrt
                    msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            try:
                fp.close()
            except Exception:
                pass
```

### 向后兼容性

- Linux/macOS：行为不变，仍使用 `fcntl.flock`。
- Windows：新增 `msvcrt.locking` 保护，从无锁升级为有锁。
- 无 `fcntl` 且无 `msvcrt` 的环境（如某些容器）：记录 warning 后降级为无锁，行为与改造前一致。

### 数据迁移

- 不需要。

### 集成风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| `msvcrt.locking` 在 Windows 上的锁粒度与 `fcntl.flock` 不同 | 中 | 中 | `msvcrt.locking` 是字节范围锁，锁定 1 字节即可；与 `fcntl.flock` 的文件级锁语义等价 |
| Windows 上的 `msvcrt` 模块在某些 Python 发行版中不可用 | 低 | 低 | 使用 `try/except ImportError` 兜底 |
| 锁释放异常导致死锁 | 低 | 高 | 使用 `contextlib.contextmanager` + `finally` 确保释放 |

---

## 变更间交互矩阵

| | 01 | 02 | 03 | 04 | 05 | 06 | 07 |
|---|---|---|---|---|---|---|---|
| **01** | -- | 无 | 无 | 弱 | 无 | 无 | 无 |
| **02** | 无 | -- | **强** | 无 | 无 | 无 | 无 |
| **03** | 无 | **强** | -- | 无 | 无 | 无 | 无 |
| **04** | 弱 | 无 | 无 | -- | 无 | 无 | 无 |
| **05** | 无 | 无 | 无 | 无 | -- | 无 | 无 |
| **06** | 无 | 无 | 无 | 无 | 无 | -- | 无 |
| **07** | 无 | 无 | 无 | 无 | 无 | 无 | -- |

- **强依赖**：02 -> 03（必须先删重复再补测试）
- **弱交互**：01 -> 04（`_all_slave_status` 中的 except 被 01 移除，04 不再需要改造该处）

---

## 综合风险评估

### 风险热力图

| 变更 | 概率 | 影响 | 风险分 | 优先级 |
|------|------|------|--------|--------|
| 01 _all_slave_status 适配 | 低 | 中 | 低 | P0 但风险可控 |
| 02 消除重复定义 | 极低 | 高 | 低 | P0 已验证一致 |
| 03 测试补全 | 低 | 低 | 极低 | P0 纯测试 |
| 04 裸 except 改造 | 低 | 中 | 低 | P1 |
| 05 SSH 密钥安全 | 中 | 中 | 中 | P1 |
| **06 返回契约统一** | **高** | **高** | **高** | **P2 建议裁剪** |
| 07 跨平台锁 | 低 | 中 | 低 | P2 |

### 建议执行策略

1. **Phase 1（Day 1）**：01 和 02 并行执行，风险低，收益高
2. **Phase 2（Day 2-4）**：02 完成后执行 03；04 和 05 可并行
3. **Phase 3（Day 5-6）**：06 建议降级为仅统一 `handshake_service.py` 的 5 处返回 dict 的调用点，跳过返回字符串的场景
4. **Phase 4（Day 6）**：07 独立执行，风险低

### 回滚优先级

| 优先级 | 变更 | 回滚成本 |
|--------|------|---------|
| 1 | 02（如引发 AttributeError） | 5 分钟 git revert |
| 2 | 01（如 5.7/8.0 下返回空） | 5 分钟 git revert |
| 3 | 05（如 SSH 连接失败） | 2 分钟配置修改 |
| 4 | 06（如前端解析异常） | 5 分钟 git revert |
| 5 | 04/07 | 低影响，可延迟回滚 |
