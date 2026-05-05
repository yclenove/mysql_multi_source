# Changelog

## v2.2.0（阶段三十三）— 工程化收尾与代码清洁

### 兼容性收尾

- `_all_slave_status()` 改用 `_replication_sql("SHOW_STATUS_ALL")` 适配函数，消除硬编码 `SHOW SLAVE STATUS`
- MySQL 8.4 LTS 不再触发无效异常，dashboard/diagnose/wizard 全链路自动受益

### 代码清洁

- 消除主文件与 mixin 的 3 处方法重复定义（`wizard_dashboard_snapshot`/`wizard_diagnose_all`/`wizard_quick_fix`），净减 162 行
- 裸 `except Exception` 从 70 处降至 26 处（降 63%），全部改为具体异常类型 + 结构化日志
- mms/ 模块统一使用 `_ok/_fail` 结构化返回（保留纯字符串日志接口不动）

### SSH 安全

- `StrictHostKeyChecking=no` 改为配置化 `ssh_strict_host_key`，默认 `accept-new`
- 首次连接自动接受主机密钥，后续连接验证已知主机，消除中间人攻击风险

### 测试加固

- diagnose_service 测试覆盖率从 34% 提升至 100%（+28 条用例）
- 总测试数：267 条，全部通过

### 跨平台

- `_with_lock` 增加 Windows `msvcrt` 文件锁支持，Windows 下配置文件并发写入受保护

### 向后兼容性

- 无 breaking change，升级无需人工干预
- SSH 配置新增 `ssh_strict_host_key` 选项，不设置时使用安全默认值

---

## v2.1.0（阶段三十二）— 安全加固与 MySQL 兼容性

### 安全加固

- **GTID 注入防护**：新增 `_validate_gtid_set()` 正则校验，在 `SET @@GLOBAL.gtid_purged` 前拦截非法输入
- **配置单密码加密**：`master_export_signed_profile` 导出时 `repl_password` 使用 Fernet 加密，导入时自动解密
- **移除 XOR fallback**：`_crypto_encrypt` 不再降级到 XOR，强制使用 Fernet；旧 XOR 密文读取兼容保留
- **密码 stdin pipe**：物理初始化 SSH 远程命令移除 `--password=...`，仅保留 `MYSQL_PWD` 环境变量

### MySQL 8.0.23+ 兼容

- 新增 `mms/replication_syntax.py`：运行时检测 MySQL 版本，8.0.23+ 自动使用新语法
  - `CHANGE REPLICATION SOURCE TO` / `START REPLICA` / `STOP REPLICA` / `SHOW REPLICA STATUS`
  - 旧版本保持 `CHANGE MASTER TO` / `START SLAVE` / `STOP SLAVE` 不变
- 主文件 9 处复制 SQL 调用统一走适配函数

### my.cnf 防覆盖

- 多源复制配置写入独立文件 `multi_source.cnf`，通过 `!include` 指令引入主 `my.cnf`
- 自动探测 include 目录（CentOS/Ubuntu/通用 fallback）
- 写入前自动备份原配置

### 测试奠基

- 新增 `tests/` 目录，10 个测试文件，236 条用例
- 覆盖 validators(100%) / crypto(90%) / config_store(92%) / handshake_service(91%) / replication_syntax(100%)
- 总体覆盖率 85%，可在无 MySQL 实例环境下运行

### 工程化

- 裸 `except Exception` 分类捕获 Top 10，新增结构化日志
- 新增 `.github/workflows/ci.yml`（Python 3.8/3.10/3.12 矩阵测试）

### 向后兼容性

- 旧 XOR 密文仍可通过 `_crypto_decrypt` 正常解密
- 旧明文配置单导入时自动加密入库
- 升级无需人工干预，配置文件自动迁移

---

## v2.0.0（阶段二十一至三十一）

### 阶段三十一：后端第一批模块化拆分

- 新增 `mms/` 包：validators / crypto / config_store / logging_audit / handshake_service

### 阶段二十一至三十

- v2.0 全面重构：Vue3 + Vite 前端、物理/逻辑双引擎初始化、向导编排
- 主库协同：健康检测、自动修复、握手会话、签名配置
- 安全审计、身份引导、新手/专家双模式
- 详见 README.md 各阶段进展记录
