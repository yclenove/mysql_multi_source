#!/usr/bin/python
# coding: utf-8

import json
import os
import re
import socket
import sys
import time
import uuid
import platform
import hashlib
import hmac
import base64
import copy
import shlex
import subprocess
import contextlib
import logging
import threading

logger = logging.getLogger("mms.main")

if "/www/server/panel/class" not in sys.path:
    sys.path.insert(0, "/www/server/panel/class")
if "/www/server/panel" not in sys.path:
    sys.path.insert(0, "/www/server/panel")

import public
import db_mysql
from mms.validators import ValidatorsMixin
from mms.crypto import CryptoMixin
from mms.config_store import ConfigStoreMixin
from mms.logging_audit import LoggingAuditMixin
from mms.handshake_service import HandshakeServiceMixin
from mms.dashboard_service import DashboardServiceMixin
from mms.diagnose_service import DiagnoseServiceMixin
from mms.replication_syntax import ReplicationSyntaxMixin

try:
    # Used to register the target DB into BaoTa's panel SQLite so it shows up
    # in the "数据库" list; optional because older BaoTa versions may not
    # ship this module in the same path.
    import database as _bt_database
except Exception:
    _bt_database = None

try:
    import fcntl as _fcntl
except Exception:
    _fcntl = None

try:
    from cryptography.fernet import Fernet, InvalidToken as _FernetInvalid
    _HAS_FERNET = True
except Exception:
    _HAS_FERNET = False
    _FernetInvalid = Exception


class mysql_multi_source_main(ValidatorsMixin, CryptoMixin, ConfigStoreMixin, LoggingAuditMixin, HandshakeServiceMixin, DashboardServiceMixin, DiagnoseServiceMixin, ReplicationSyntaxMixin):
    config_path = "/www/server/panel/plugin/mysql_multi_source/multi_source_info.json"
    config_lock_path = "/www/server/panel/plugin/mysql_multi_source/multi_source_info.lock"
    log_dir = "/www/server/panel/plugin/mysql_multi_source/log"
    bootstrap_root = "/www/server/panel/plugin/mysql_multi_source/bootstrap_data"
    task_stale_timeout = 120
    sign_secret_path = "/www/server/panel/plugin/mysql_multi_source/profile_sign.key"
    crypto_key_path = "/www/server/panel/plugin/mysql_multi_source/secret.key"
    mysql_cnf_path = "/etc/my.cnf"
    plugin_root = "/www/server/panel/plugin/mysql_multi_source"
    CONFIG_SCHEMA_VERSION = "2.0.0"
    CRYPTO_PREFIX = "enc:v1:"

    def __init__(self):
        pass

    def _mysql(self):
        return db_mysql.panelMysql()

    # === basic escaping ===

    def _sql_escape(self, value):
        """Conservative escaping for literals embedded in SQL string."""
        return str(value).replace("\\", "\\\\").replace("'", "\\'")

    def _shell_quote(self, value):
        """Safe shell quoting via shlex; use for every shell interpolation."""
        return shlex.quote(str(value))

    def _mysql_password_literal(self, password):
        """Encode password as MySQL hex literal to avoid quoting pitfalls.

        Produces a form like X'48656C6C6F' which works in IDENTIFIED BY VALUES
        contexts; for IDENTIFIED BY we must still use a quoted string, so we
        keep this helper available and fall back to CREATE USER with
        parameterized style in callers.
        """
        raw = str(password).encode("utf-8")
        return "0x" + raw.hex().upper()

    # === file lock: atomic read/modify/write of config ===

    @contextlib.contextmanager
    def _with_lock(self):
        self._ensure_dirs()
        fp = None
        if _fcntl is not None:
            try:
                fp = open(self.config_lock_path, "a+")
                _fcntl.flock(fp.fileno(), _fcntl.LOCK_EX)
            except Exception:
                fp = None
        try:
            yield
        finally:
            if fp is not None:
                try:
                    _fcntl.flock(fp.fileno(), _fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    fp.close()
                except Exception:
                    pass

    # === subprocess: run shell with env, no password in argv ===

    def _run_shell(self, cmd, env_extra=None, timeout=1800, cwd=None, shell=False):
        """Run a command returning {code, stdout, stderr}.

        When shell=False, cmd must be a list. When shell=True, cmd is a string
        that must already be properly quoted by the caller (use _shell_quote).
        Passwords must be passed via env_extra; never embed in cmd.
        """
        env = os.environ.copy()
        if env_extra:
            for k, v in env_extra.items():
                env[k] = str(v)
        try:
            p = subprocess.Popen(
                cmd,
                shell=shell,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=cwd,
                universal_newlines=True,
            )
            stdout, stderr = p.communicate(timeout=timeout)
            return {"code": int(p.returncode or 0), "stdout": stdout or "", "stderr": stderr or ""}
        except subprocess.TimeoutExpired:
            try:
                p.kill()
            except Exception:
                pass
            return {"code": 124, "stdout": "", "stderr": "command timeout after {}s".format(timeout)}
        except Exception as ex:
            return {"code": 1, "stdout": "", "stderr": str(ex)}

    def _ok(self, data=None, message="ok", code="OK"):
        payload = data if data is not None else {}
        if isinstance(payload, dict):
            payload.setdefault("message", message)
            payload.setdefault("code", code)
        return public.returnMsg(True, payload)

    def _fail(self, message, code="ERR_GENERIC", data=None):
        payload = {"message": str(message), "code": code}
        if isinstance(data, dict):
            payload.update(data)
        return public.returnMsg(False, payload)

    def _exec_sql(self, sql):
        mysql_obj = self._mysql()
        return mysql_obj.execute(sql)

    def _query_sql(self, sql):
        mysql_obj = self._mysql()
        result = mysql_obj.query(sql)
        if not isinstance(result, (list, tuple)):
            return []
        return result

    def _get_source_status(self, channel_name):
        safe_channel = self._sql_escape(channel_name)
        sql = self._replication_sql("SHOW_STATUS", channel=safe_channel)
        try:
            rows = self._query_sql(sql)
            if not rows:
                return {
                    "running": False,
                    "io_running": "No",
                    "sql_running": "No",
                    "seconds_behind": None,
                    "last_error": "未获取到复制状态，可能尚未配置该 channel",
                }

            row = rows[0]
            # 宝塔不同 MySQL 驱动返回结构可能不同，这里做兼容处理
            if isinstance(row, dict):
                io_running = row.get("Slave_IO_Running", row.get("Replica_IO_Running", "No"))
                sql_running = row.get("Slave_SQL_Running", row.get("Replica_SQL_Running", "No"))
                seconds_behind = row.get("Seconds_Behind_Master", row.get("Seconds_Behind_Source"))
                last_error = row.get("Last_Error", "")
            else:
                io_running = "Unknown"
                sql_running = "Unknown"
                seconds_behind = None
                last_error = ""

            return {
                "running": io_running == "Yes" and sql_running == "Yes",
                "io_running": io_running,
                "sql_running": sql_running,
                "seconds_behind": seconds_behind,
                "last_error": last_error,
            }
        except Exception as ex:
            return {
                "running": False,
                "io_running": "No",
                "sql_running": "No",
                "seconds_behind": None,
                "last_error": "状态查询失败: {}".format(ex),
            }

    def _now(self):
        return int(time.time())

    def _decrypted_password(self, source):
        """Return plaintext repl_password from a source record (supports enc)."""
        if not isinstance(source, dict):
            return ""
        return self._crypto_decrypt(source.get("repl_password", ""))

    def _find_source(self, data, source_id):
        for item in data.get("sources", []):
            if item.get("source_id") == source_id:
                return item
        return None

    def _collect_target_db_conflicts(self, mappings, exclude_source_id=""):
        """Check whether target_db names would collide with other sources or themselves."""
        data = self._load_config()
        exclude_source_id = str(exclude_source_id or "").strip()
        normalized = []
        seen_in_request = {}
        conflicts = []

        for idx, m in enumerate(mappings or []):
            if not isinstance(m, dict):
                continue
            source_db = str(m.get("source_db", "")).strip()
            target_db = str(m.get("target_db", "")).strip()
            if not source_db or not target_db:
                continue
            normalized.append({"source_db": source_db, "target_db": target_db})
            duplicate_indexes = seen_in_request.setdefault(target_db, [])
            duplicate_indexes.append(idx)

        for target_db, indexes in seen_in_request.items():
            if len(indexes) > 1:
                conflicts.append({
                    "target_db": target_db,
                    "type": "request_duplicate",
                    "source_ids": [],
                    "mappings": [normalized[i] for i in indexes if i < len(normalized)],
                    "message": "本次选择中有多个来源库映射到了同一个目标库",
                })

        existing_by_target = {}
        for src in data.get("sources", []) or []:
            src_id = str(src.get("source_id", "")).strip()
            if exclude_source_id and src_id == exclude_source_id:
                continue
            for mapping in src.get("db_mappings", []) or []:
                target_db = str(mapping.get("target_db", "")).strip()
                if not target_db:
                    continue
                existing_by_target.setdefault(target_db, []).append({
                    "source_id": src_id,
                    "source_db": str(mapping.get("source_db", "")).strip(),
                    "target_db": target_db,
                })

        for item in normalized:
            target_db = item.get("target_db")
            matched = existing_by_target.get(target_db) or []
            if not matched:
                continue
            conflicts.append({
                "target_db": target_db,
                "type": "existing_target_db",
                "source_ids": [x.get("source_id") for x in matched if x.get("source_id")],
                "mappings": matched,
                "message": "目标库名已被其他来源占用",
            })

        deduped = []
        seen_keys = set()
        for item in conflicts:
            key = "{}|{}|{}".format(
                item.get("type", ""),
                item.get("target_db", ""),
                ",".join(sorted(item.get("source_ids") or [])),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(item)
        return deduped

    def _find_bootstrap_task(self, data, task_id):
        for task in data.get("bootstrap_tasks", []):
            if task.get("task_id") == task_id:
                return task
        return None

    def _heartbeat_task(self, task):
        task["last_heartbeat"] = self._now()
        task["updated_at"] = self._now()

    def _check_command_exists(self, command_name):
        out = public.ExecShell("command -v {}".format(command_name))[0].strip()
        return bool(out)

    def _is_root_user(self):
        try:
            return os.geteuid() == 0
        except Exception:
            out = public.ExecShell("id -u")[0].strip()
            return out == "0"

    def _detect_os_family(self):
        os_release = public.ReadFile("/etc/os-release") or ""
        lower = os_release.lower()
        if "ubuntu" in lower or "debian" in lower:
            return "debian"
        if "centos" in lower or "rocky" in lower or "almalinux" in lower or "rhel" in lower or "fedora" in lower:
            return "redhat"
        # 兜底
        system_name = platform.system().lower()
        if "linux" in system_name:
            return "linux"
        return "unknown"

    def _build_tool_install_cmd(self, tool_name):
        os_family = self._detect_os_family()
        if tool_name not in ["xtrabackup", "mariabackup"]:
            return ""

        if os_family == "debian":
            if tool_name == "xtrabackup":
                return self._build_xtrabackup_install_debian()
            return self._build_mariabackup_install_debian()
        if os_family in ["redhat", "linux"]:
            if tool_name == "xtrabackup":
                return self._build_xtrabackup_install_rhel()
            return self._build_mariabackup_install_rhel()
        return ""

    def _build_xtrabackup_install_debian(self):
        # 多路回退：apt 直装 → percona-release 仓库(code-name / generic) → 再装；
        # 同时打印每一步方便看 install.log。
        return (
            "set +e; "
            "echo '[step] ensure base tools'; "
            "export DEBIAN_FRONTEND=noninteractive; "
            "apt-get update -y; "
            "apt-get install -y wget gnupg2 lsb-release curl ca-certificates; "
            "echo '[step] try direct install'; "
            "if apt-get install -y percona-xtrabackup-80; then echo '[done] via existing repo'; exit 0; fi; "
            "if apt-get install -y percona-xtrabackup-24; then echo '[done] via existing repo'; exit 0; fi; "
            "CODENAME=$(lsb_release -sc 2>/dev/null); "
            "echo \"[step] codename=$CODENAME; adding percona-release repo\"; "
            "TMPDEB=$(mktemp /tmp/percona-release.XXXXXX.deb); "
            "for URL in "
            "\"https://repo.percona.com/apt/percona-release_latest.${CODENAME}_all.deb\" "
            "\"https://repo.percona.com/apt/percona-release_latest.generic_all.deb\"; do "
            "  echo \"[step] fetch $URL\"; "
            "  if wget -qO \"$TMPDEB\" \"$URL\"; then "
            "    dpkg -i \"$TMPDEB\" 2>&1 || apt-get -f install -y; "
            "    break; "
            "  fi; "
            "done; "
            "percona-release enable-only tools release 2>&1 || percona-release enable tools 2>&1 || true; "
            "apt-get update -y; "
            "echo '[step] install after enabling repo'; "
            "apt-get install -y percona-xtrabackup-80 && exit 0; "
            "apt-get install -y percona-xtrabackup-24 && exit 0; "
            "echo '[fail] all install routes failed; 建议改用 mariabackup 按钮安装 mariadb-backup，或改用逻辑(mysqldump)模式'; "
            "exit 1"
        )

    def _build_mariabackup_install_debian(self):
        return (
            "set +e; "
            "export DEBIAN_FRONTEND=noninteractive; "
            "apt-get update -y; "
            "if apt-get install -y mariadb-backup; then exit 0; fi; "
            "if apt-get install -y mariadb-backup-10.11; then exit 0; fi; "
            "if apt-get install -y mariadb-backup-10.6; then exit 0; fi; "
            "if apt-get install -y mariadb-backup-10.5; then exit 0; fi; "
            "if apt-get install -y mariadb-backup-10.4; then exit 0; fi; "
            "echo '[fail] no mariadb-backup variant found in repos'; exit 1"
        )

    def _build_xtrabackup_install_rhel(self):
        return (
            "set +e; "
            "echo '[step] try direct install'; "
            "(yum install -y percona-xtrabackup-80 || dnf install -y percona-xtrabackup-80) && exit 0; "
            "(yum install -y percona-xtrabackup-24 || dnf install -y percona-xtrabackup-24) && exit 0; "
            "echo '[step] install percona-release'; "
            "(rpm -q percona-release >/dev/null 2>&1 || yum install -y https://repo.percona.com/yum/percona-release-latest.noarch.rpm || dnf install -y https://repo.percona.com/yum/percona-release-latest.noarch.rpm); "
            "percona-release enable-only tools release 2>&1 || percona-release enable tools 2>&1 || true; "
            "yum clean all 2>/dev/null; dnf clean all 2>/dev/null; "
            "(yum install -y percona-xtrabackup-80 || dnf install -y percona-xtrabackup-80) && exit 0; "
            "(yum install -y percona-xtrabackup-24 || dnf install -y percona-xtrabackup-24) && exit 0; "
            "echo '[fail] all install routes failed; 建议改用 mariabackup 按钮安装 mariadb-backup，或改用逻辑(mysqldump)模式'; exit 1"
        )

    def _build_mariabackup_install_rhel(self):
        return (
            "set +e; "
            "(yum install -y mariadb-backup || dnf install -y mariadb-backup) && exit 0; "
            "(yum install -y MariaDB-backup || dnf install -y MariaDB-backup) && exit 0; "
            "echo '[fail] no mariadb-backup available'; exit 1"
        )

    def _task_step_update(self, data, task, step, progress):
        task_id = task.get("task_id")
        source_id = task.get("source_id")
        latest = self._load_config()
        latest_task = self._find_bootstrap_task(latest, task_id)
        if not latest_task:
            return
        if source_id and not self._find_source(latest, source_id):
            latest_task["status"] = "cancelled"
            latest_task["current_step"] = "来源已删除，任务取消"
            latest_task["error"] = "source removed"
            self._heartbeat_task(latest_task)
            self._save_config(latest)
            self._append_task_log(task_id, "source removed, cancel task")
            return
        latest_task["current_step"] = step
        latest_task["progress"] = progress
        self._heartbeat_task(latest_task)
        self._save_config(latest)
        self._append_task_log(task_id, "step={} progress={}".format(step, progress))

    def _simulate_or_exec(self, source_id, cmd, allow_fail=False):
        # 当前插件以编排器为主，默认执行轻量验证命令并保留可替换的真实执行点
        self._append_log(source_id, "执行命令: {}".format(cmd))
        out, err = public.ExecShell(cmd)
        if err and not allow_fail:
            raise Exception(err.strip())
        return out, err

    def _get_local_mysql_root_password(self):
        # Different BaoTa versions may store mysql root password in different
        # places; some setups intentionally keep it empty (socket auth / empty
        # root password). We therefore return '' as a valid outcome rather
        # than treating it as fatal.
        try:
            v = public.M("config").where("id=?", (1,)).getField("mysql_root")
            if v is not None:
                v = str(v).strip()
                if v:
                    return v
        except Exception:
            pass
        # Fallback: config file (best-effort)
        try:
            cfg_path = "/www/server/panel/config/config.json"
            if os.path.exists(cfg_path):
                raw = public.ReadFile(cfg_path) or ""
                if raw:
                    cfg = json.loads(raw)
                    v = str(cfg.get("mysql_root", "")).strip()
                    if v:
                        return v
        except Exception:
            pass
        return ""

    def _register_db_in_panel(self, db_name, source_id=""):
        """Ensure BaoTa's panel SQLite knows about ``db_name``.

        BaoTa's "数据库 → MySQL" list only shows databases present in the panel
        SQLite table ``databases``. Databases created directly via
        ``CREATE DATABASE`` (like the ones our bootstrap produces) are
        invisible there, which makes users think the sync isn't real.

        Strategy:
          * If BaoTa's ``database`` module is available, try its ``SetupDatabase``
            / ``AddDatabase`` APIs (these fail if the DB already exists, which
            is fine -- we catch and fall through).
          * Otherwise (or on failure) write directly into the ``databases``
            table via ``public.M``, which BaoTa's UI reads from.

        Returns a short human-readable note describing what happened, or ''
        when the DB was already registered.
        """
        if not db_name or not self._validate_mysql_scope_name(db_name):
            return ""
        try:
            existing = public.M("databases").where("name=?", (db_name,)).count()
            if existing and int(existing) > 0:
                return ""
        except Exception as e:
            logger.debug("查询数据库注册状态失败: %s", e)
            existing = 0

        ps_note = "mysql_multi_source 同步库"
        if source_id:
            ps_note += "（源: {}）".format(source_id)

        # --- Path 1: BaoTa's database module (best-effort) ---------------
        if _bt_database is not None:
            try:
                root_pwd = self._get_local_mysql_root_password() or ""
                get_obj = public.to_dict_obj({
                    "name": db_name,
                    "codeing": "utf8mb4",
                    "db_user": db_name[:32],
                    "password": root_pwd or "mms_readonly",
                    "dataAccess": "127.0.0.1",
                    "sid": "0",
                    "address": "127.0.0.1",
                    "ps": ps_note,
                    "dtype": "MySQL",
                })
                db_helper = _bt_database.database()
                if hasattr(db_helper, "SetupDatabase"):
                    _ = db_helper.SetupDatabase(get_obj)
                elif hasattr(db_helper, "AddDatabase"):
                    _ = db_helper.AddDatabase(get_obj)
                if public.M("databases").where("name=?", (db_name,)).count():
                    return "已注册到宝塔数据库列表: {}".format(db_name)
            except Exception:
                pass

        # --- Path 2: raw insert into panel SQLite ------------------------
        try:
            now_ts = time.strftime("%Y-%m-%d %X", time.localtime())
            record = {
                "pid": 0,
                "name": db_name,
                "type": "MySQL",
                "sid": 0,
                "username": db_name[:32],
                "password": "",
                "accept": "127.0.0.1",
                "ps": ps_note,
                "addtime": now_ts,
            }
            # Some BaoTa schemas include extra columns (e.g. ``quota`` or
            # ``login_name``). Insert best-effort and fall back to a smaller
            # record if the insert fails.
            for trim in [0, 1, 2]:
                slim = dict(record)
                if trim >= 1:
                    slim.pop("ps", None)
                if trim >= 2:
                    slim.pop("accept", None)
                try:
                    public.M("databases").insert(slim)
                    return "已写入宝塔数据库列表（最小字段={}）: {}".format(trim, db_name)
                except Exception as e:
                    logger.debug("写入宝塔数据库列表失败 (trim=%d): %s", trim, e)
                    continue
        except Exception as e:
            logger.debug("注册数据库 %s 到宝塔面板失败: %s", db_name, e)
        return ""

    def _resolve_task_mode(self, task_mode):
        mode = str(task_mode or "auto").strip().lower()
        if mode == "physical":
            return "physical"
        if mode == "logical":
            return "logical"
        # auto fallback
        if self._check_command_exists("xtrabackup") or self._check_command_exists("mariabackup"):
            return "physical"
        return "logical"

    def _parse_version_major_minor(self, ver_text):
        s = str(ver_text or "")
        m = re.search(r"(\d+)\.(\d+)", s)
        if not m:
            return None
        try:
            return int(m.group(1)), int(m.group(2))
        except Exception:
            return None

    def _is_xtrabackup_mysql_compatible(self, xb_ver_text, mysql_ver_text):
        xb = str(xb_ver_text or "").lower()
        my = str(mysql_ver_text or "").lower()
        my_mm = self._parse_version_major_minor(my)
        if not my_mm:
            return True, ""
        if "xtrabackup version 8." in xb and my_mm[0] == 5:
            return False, "主库是 MySQL {}，但 xtrabackup 为 8.0".format(mysql_ver_text)
        if ("xtrabackup version 2.4" in xb or "xtrabackup 2.4" in xb) and my_mm[0] >= 8:
            return False, "主库是 MySQL {}，但 xtrabackup 为 2.4".format(mysql_ver_text)
        return True, ""

    def _run_physical_bootstrap(self, source, task):
        """Real physical bootstrap pipeline using xtrabackup/mariabackup + SSH.

        High-level:
          1. Detect local tool + SSH reachability to master host.
          2. Stream backup from master via `ssh ... xtrabackup --backup --stream=xbstream` to local staging dir.
          3. Run `xtrabackup --prepare` locally to produce a consistent snapshot.
          4. Because live datadir replacement is unsafe inside a plugin, after
             prepare we record the staged path and hand off to the logical
             pipeline for the final per-database import. This gives the speed
             benefit of a physical, point-in-time consistent backup from the
             master without touching the running MySQL datadir.
          5. On any failure (missing tool, SSH, backup, prepare), fall back
             gracefully to the logical bootstrap with clear audit logs.
        """
        source_id = source.get("source_id")
        task_id = task.get("task_id")
        tool = None
        if self._check_command_exists("xtrabackup"):
            tool = "xtrabackup"
        elif self._check_command_exists("mariabackup"):
            tool = "mariabackup"
        if not tool:
            self._append_task_log(task_id, "[physical] 未检测到 xtrabackup/mariabackup，自动降级 logical")
            task["effective_mode_note"] = "fallback_logical_no_tool"
            return self._run_logical_bootstrap(source, task)

        source_host = source.get("master_host", "")
        source_port = int(source.get("master_port", 3306))
        user = source.get("repl_user", "")
        pwd = self._decrypted_password(source)
        mappings = source.get("db_mappings", []) or []
        dbs_list = ",".join([str(m.get("source_db", "")).strip() for m in mappings if m.get("source_db")])
        if not dbs_list:
            raise Exception("未配置库映射")

        # SSH preflight; use BatchMode=yes so missing keys fail fast
        ssh_user = "root"
        preflight = self._run_shell(
            [
                "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=5",
                "{}@{}".format(ssh_user, source_host),
                "command -v {} || echo NO_TOOL".format(tool),
            ],
            timeout=15,
        )
        if preflight["code"] != 0 or "NO_TOOL" in preflight.get("stdout", ""):
            err_txt = (preflight.get("stderr") or "") + (preflight.get("stdout") or "")
            if "NO_TOOL" in (preflight.get("stdout") or ""):
                friendly = (
                    "主库服务器未安装 {tool}（物理模式需要在主库上执行 {tool} --backup），"
                    "自动改用 logical 模式。"
                    "如需物理模式：去主库插件「帮我成为主库 → 物理模式 · 安装 xtrabackup」一键安装。"
                ).format(tool=tool)
            elif "permission denied" in err_txt.lower() or "publickey" in err_txt.lower():
                friendly = "主库未配置 SSH 免密到从库，物理模式不可用，自动改用 logical（这是正常流程，不是故障）"
            elif "timed out" in err_txt.lower() or "connection refused" in err_txt.lower() or preflight["code"] == 255:
                friendly = "无法 SSH 到主库（{}），自动改用 logical".format(source_host)
            else:
                friendly = "物理模式预检失败，自动改用 logical"
            self._append_task_log(task_id, "[physical→logical] " + friendly)
            task["effective_mode_note"] = "fallback_logical_ssh_unavailable"
            return self._run_logical_bootstrap(source, task)

        # xtrabackup major version must match MySQL major family:
        #   - MySQL 5.7  <-> xtrabackup 2.4
        #   - MySQL 8.0  <-> xtrabackup 8.0
        if tool == "xtrabackup":
            remote_mysql_ver = ""
            remote_tool_ver = ""
            local_tool_ver = ""
            try:
                rv = self._run_shell(
                    [
                        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
                        "{}@{}".format(ssh_user, source_host),
                        "mysql -Nse \"SELECT @@version\" 2>/dev/null || true",
                    ],
                    timeout=12,
                )
                remote_mysql_ver = str(rv.get("stdout") or "").strip()
            except Exception:
                remote_mysql_ver = ""
            try:
                rv = self._run_shell(
                    [
                        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
                        "{}@{}".format(ssh_user, source_host),
                        "{} --version 2>/dev/null".format(tool),
                    ],
                    timeout=12,
                )
                _s = str(rv.get("stdout") or "").strip()
                remote_tool_ver = _s.splitlines()[0].strip() if _s else ""
            except Exception:
                remote_tool_ver = ""
            try:
                lv = self._run_shell([tool, "--version"], timeout=8)
                _s = str(lv.get("stdout") or "").strip()
                local_tool_ver = _s.splitlines()[0].strip() if _s else ""
            except Exception:
                local_tool_ver = ""

            if remote_mysql_ver:
                ok_r, why_r = self._is_xtrabackup_mysql_compatible(remote_tool_ver, remote_mysql_ver)
                ok_l, why_l = self._is_xtrabackup_mysql_compatible(local_tool_ver, remote_mysql_ver)
                if not ok_r or not ok_l:
                    self._append_task_log(
                        task_id,
                        "[physical→logical] xtrabackup 与主库版本不匹配，自动改用 logical\n"
                        "主库 MySQL: {}\n主库 xtrabackup: {}\n从库 xtrabackup: {}\n{}\n{}\n"
                        "建议：MySQL 5.7 请安装 percona-xtrabackup-24；MySQL 8.0 请安装 percona-xtrabackup-80".format(
                            remote_mysql_ver or "unknown",
                            remote_tool_ver or "unknown",
                            local_tool_ver or "unknown",
                            ("主库检查: " + why_r) if why_r else "",
                            ("从库检查: " + why_l) if why_l else "",
                        ),
                    )
                    task["effective_mode_note"] = "fallback_logical_xtrabackup_version_incompatible"
                    return self._run_logical_bootstrap(source, task)

        task_dir = os.path.join(self.bootstrap_root, task_id)
        backup_dir = os.path.join(task_dir, "xb_backup")
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)

        # Log paths for full-fidelity debugging. Users can open these via the
        # plugin's task log viewer instead of relying on truncated snippets.
        backup_log = os.path.join(task_dir, "xb_backup.stderr.log")
        prepare_log = os.path.join(task_dir, "xb_prepare.stderr.log")

        def _dump_stderr(path, content):
            try:
                with open(path, "wb") as f:
                    if isinstance(content, str):
                        f.write(content.encode("utf-8", errors="replace"))
                    else:
                        f.write(content or b"")
            except (IOError, OSError) as e:
                logger.debug("写入备份日志 %s 失败: %s", path, e)

        # Stream backup over SSH.
        # NOTE:
        #   Password is passed via MYSQL_PWD environment variable only,
        #   NOT via --password= argument. This prevents the password from
        #   appearing in the process list on the master host.
        remote_err_path = "/tmp/mms_xb_{}.err".format(task_id)
        remote_cmd = (
            "MYSQL_PWD={pwd_q} {tool} --backup --stream=xbstream "
            "--user={user_q} --host=127.0.0.1 --port={port} "
            "--databases={dbs_q} 2>{err_q}"
        ).format(
            pwd_q=self._shell_quote(pwd),
            tool=tool,
            user_q=self._shell_quote(user),
            port=source_port,
            dbs_q=self._shell_quote(dbs_list),
            err_q=self._shell_quote(remote_err_path),
        )
        pipeline = "ssh -o BatchMode=yes -o StrictHostKeyChecking=no {tgt} {remote} | xbstream -x -C {dir}".format(
            tgt=self._shell_quote("{}@{}".format(ssh_user, source_host)),
            remote=self._shell_quote(remote_cmd),
            dir=self._shell_quote(backup_dir),
        )
        self._append_task_log(task_id, "[physical] 开始流式备份到: {}".format(backup_dir))
        # IMPORTANT: use bash + pipefail, otherwise shell pipelines may return
        # the exit code of xbstream only, hiding ssh/xtrabackup failures.
        r = self._run_shell(
            ["bash", "-lc", "set -o pipefail; " + pipeline],
            timeout=7200,
        )
        _dump_stderr(backup_log, r.get("stderr") or "")
        if r["code"] != 0:
            err = (r.get("stderr") or "")[:800]
            self._append_task_log(task_id, "[physical] backup 失败，降级 logical：{}".format(err))
            self._append_task_log(
                task_id,
                "[physical] 完整日志: {} （主库侧: {}）".format(backup_log, remote_err_path),
            )
            task["effective_mode_note"] = "fallback_logical_backup_fail"
            return self._run_logical_bootstrap(source, task)
        self._append_task_log(task_id, "[physical] 流式备份完成")

        # Inspect the backup dir so we know partial/full and size, which helps
        # diagnose prepare failures (e.g. empty backup = nothing to apply).
        try:
            xb_entries = os.listdir(backup_dir)
            xb_total_mb = 0.0
            for root_d, _dirs, files in os.walk(backup_dir):
                for fn in files:
                    try:
                        xb_total_mb += os.path.getsize(os.path.join(root_d, fn)) / 1024.0 / 1024.0
                    except Exception:
                        pass
            has_checkpoints = os.path.exists(os.path.join(backup_dir, "xtrabackup_checkpoints"))
            self._append_task_log(
                task_id,
                "[physical] 备份目录内容: {} 项, 约 {:.2f} MB, checkpoints={} (顶层: {})".format(
                    len(xb_entries), xb_total_mb, "yes" if has_checkpoints else "no",
                    ", ".join(xb_entries[:6]) + ("..." if len(xb_entries) > 6 else "")
                ),
            )
            if not has_checkpoints:
                # Physical stream produced no valid xtrabackup metadata.
                # Try to fetch remote error file for direct diagnosis.
                try:
                    remote_err = self._run_shell(
                        [
                            "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
                            "{}@{}".format(ssh_user, source_host),
                            "tail -n 80 {} 2>/dev/null || echo '(主库无 {})'".format(
                                self._shell_quote(remote_err_path),
                                self._shell_quote(remote_err_path),
                            ),
                        ],
                        timeout=12,
                    )
                    remote_tail = (remote_err.get("stdout") or "").strip()
                except Exception:
                    remote_tail = ""
                if remote_tail:
                    self._append_task_log(task_id, "[physical] 主库 /tmp/mms_xb.err 尾部:\n{}".format(remote_tail))
                self._append_task_log(task_id, "[physical] 未生成 xtrabackup_checkpoints，判定物理备份无效，自动改用 logical")
                task["effective_mode_note"] = "fallback_logical_empty_physical_backup"
                return self._run_logical_bootstrap(source, task)
        except Exception as ex:
            self._append_task_log(task_id, "[physical] 无法枚举备份目录: {}".format(ex))

        # Prepare (apply-log). For partial (--databases=) backups on xtrabackup 8.0+
        # the --export flag is required so prepare generates per-table .cfg/.ibd
        # files usable for the subsequent import. We try --prepare --export first;
        # if that fails (older xtrabackup or full-instance layout), retry plain
        # --prepare. The full stderr is always written to prepare_log for diag.
        def _try_prepare(extra_args):
            return self._run_shell(
                [tool, "--prepare"] + extra_args + ["--target-dir=" + backup_dir],
                timeout=3600,
            )

        self._append_task_log(task_id, "[physical] prepare: 尝试 --prepare --export")
        r = _try_prepare(["--export"])
        if r["code"] != 0:
            _dump_stderr(prepare_log + ".export", r.get("stderr") or "")
            self._append_task_log(task_id, "[physical] --export 方式失败，回退普通 --prepare")
            r = _try_prepare([])
        _dump_stderr(prepare_log, r.get("stderr") or "")

        if r["code"] != 0:
            # Keep a longer snippet so the real error is visible in the UI log.
            raw = r.get("stderr") or ""
            snippet = raw[-1500:] if len(raw) > 1500 else raw
            self._append_task_log(
                task_id,
                "[physical] prepare 失败，降级 logical（完整日志: {}）。\n错误尾部：\n{}".format(
                    prepare_log, snippet,
                ),
            )
            task["effective_mode_note"] = "fallback_logical_prepare_fail"
            return self._run_logical_bootstrap(source, task)
        self._append_task_log(task_id, "[physical] prepare 完成，数据已物理化到本地")

        # NOTE: We intentionally do NOT replace the replica's datadir here;
        # that needs `systemctl stop mysql` + chown + rsync, which is too risky
        # inside a panel plugin. We still complete the task by routing the
        # per-database import through the logical pipeline. The physical
        # snapshot remains available under ``backup_dir`` for manual use.
        task["physical_stage_dir"] = backup_dir
        task["effective_mode_note"] = "physical_staged_logical_import"
        self._append_task_log(
            task_id,
            "[physical] 物理备份已 staged，为保证数据落地使用 logical 通道完成按映射导入",
        )
        return self._run_logical_bootstrap(source, task)

    def _run_logical_bootstrap(self, source, task):
        source_id = source.get("source_id")
        task_id = task.get("task_id")
        self._append_task_log(task_id, "进入 logical 初始化分支")

        self._append_task_log(task_id, "检查 mysqldump 是否可用...")
        if not self._check_command_exists("mysqldump"):
            raise Exception("未检测到 mysqldump，无法执行逻辑初始化")
        self._append_task_log(task_id, "检查 mysql 客户端是否可用...")
        if not self._check_command_exists("mysql"):
            raise Exception("未检测到 mysql 客户端，无法执行逻辑初始化")

        mappings = source.get("db_mappings", [])
        if not mappings:
            raise Exception("未配置库映射")

        source_host = source.get("master_host", "")
        source_port = int(source.get("master_port", 3306))
        source_user = source.get("repl_user", "")
        source_pwd = self._decrypted_password(source)
        self._append_task_log(task_id, "读取本地 MySQL root 密码...")
        local_root_pwd = self._get_local_mysql_root_password() or ""
        if local_root_pwd:
            self._append_task_log(task_id, "本地 root 密码读取完成，共 {} 个库待处理".format(len(mappings)))
        else:
            self._append_task_log(
                task_id,
                "未读取到本地 root 密码，将尝试空密码/本机默认认证继续导入（若失败请在宝塔数据库设置 root 密码）"
            )

        task_dir = os.path.join(self.bootstrap_root, task_id)
        if not os.path.exists(task_dir):
            os.makedirs(task_dir)

        # Background heartbeat so UI progress bar stays alive even when dump blocks.
        hb_stop = threading.Event()
        def _heartbeat_loop():
            while not hb_stop.is_set():
                try:
                    def _beat(d):
                        t = self._find_bootstrap_task(d, task_id)
                        if t and t.get("status") == "running":
                            t["last_heartbeat"] = self._now()
                    self._update_config(_beat)
                except Exception:
                    pass
                hb_stop.wait(5)
        hb_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        hb_thread.start()

        # Capture master GTID executed position BEFORE the first dump so that
        # we can later SET @@GLOBAL.gtid_purged and START REPLICA without the
        # replica trying to re-apply historical events. Best-effort: if the
        # capture fails we continue, but replication will likely need manual
        # catchup later.
        try:
            captured_gtid = self._query_master_gtid_executed(source_host, source_port, source_user, source_pwd)
            if captured_gtid is not None:
                task["master_gtid_at_dump"] = captured_gtid
                self._append_task_log(
                    task_id,
                    "[gtid] 主库 gtid_executed 捕获完成: {}".format(captured_gtid or "(空)"),
                )
            else:
                self._append_task_log(task_id, "[gtid] 未能从主库读取 gtid_executed，稍后启动通道可能遇到追数异常")
        except Exception as ex:
            self._append_task_log(task_id, "[gtid] 捕获失败: {}".format(ex))

        try:
            return self._run_logical_bootstrap_core(
                source, task, mappings, source_host, source_port, source_user, source_pwd,
                local_root_pwd, task_dir,
            )
        finally:
            hb_stop.set()

    def _query_master_gtid_executed(self, host, port, user, pwd):
        """Return master's @@GLOBAL.gtid_executed (possibly empty string) or None on error.

        Uses the mysql CLI so we don't depend on pymysql/MySQLdb being importable
        from the plugin context; password is passed via MYSQL_PWD env to avoid
        argv leakage.
        """
        try:
            cmd = [
                "mysql",
                "--host=" + str(host),
                "--port=" + str(int(port)),
                "--user=" + str(user),
                "--skip-column-names",
                "--batch",
                "--connect-timeout=8",
                "-e", "SELECT @@GLOBAL.gtid_executed",
            ]
            env = dict(os.environ, MYSQL_PWD=str(pwd or ""))
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            try:
                out, err = p.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
                return None
            if p.returncode != 0:
                return None
            val = (out or b"").decode("utf-8", errors="replace").strip()
            # mysql CLI prints NULL as "NULL" when value is NULL; MySQL should
            # never return NULL for gtid_executed but handle it just in case.
            if val.upper() == "NULL":
                return ""
            return val
        except Exception as e:
            logger.debug("查询主库 gtid_executed 失败: %s", e)
            return None

    def _run_logical_bootstrap_core(self, source, task, mappings, source_host, source_port,
                                     source_user, source_pwd, local_root_pwd, task_dir):
        source_id = source.get("source_id")
        task_id = task.get("task_id")
        total = len([m for m in mappings if m.get("source_db") and m.get("target_db")]) or 1
        idx = 0
        for m in mappings:
            source_db = str(m.get("source_db", "")).strip()
            target_db = str(m.get("target_db", "")).strip()
            if not source_db or not target_db:
                continue
            if not self._validate_mysql_scope_name(source_db):
                raise Exception("非法 source_db: {}".format(source_db))
            if not self._validate_mysql_scope_name(target_db):
                raise Exception("非法 target_db: {}".format(target_db))

            dump_file = os.path.join(task_dir, "{}__to__{}.sql".format(source_db, target_db))

            create_db_sql = "CREATE DATABASE IF NOT EXISTS `{}` CHARACTER SET utf8mb4".format(target_db)
            self._exec_sql(create_db_sql)
            self._append_task_log(task_id, "确认目标库存在: {}".format(target_db))
            try:
                reg_note = self._register_db_in_panel(target_db, source_id=source.get("source_id"))
                if reg_note:
                    self._append_task_log(task_id, "[panel] " + reg_note)
            except Exception as ex:
                self._append_task_log(task_id, "[panel] 注册到宝塔数据库列表失败（不影响同步）: {}".format(ex))

            dump_progress = 30 + int((idx / total) * 25)
            self._task_step_update(None, task, "备份 {} ({}/{})".format(source_db, idx + 1, total), dump_progress)
            self._append_task_log(task_id, "开始 mysqldump: {} -> {}".format(source_db, dump_file))

            # dump: password via MYSQL_PWD env; stdout → dump_file
            with open(dump_file, "wb") as fout:
                p = subprocess.Popen(
                    [
                        "mysqldump",
                        "--single-transaction", "--skip-lock-tables", "--set-gtid-purged=OFF",
                        "--no-tablespaces", "--quick",
                        "--host=" + source_host,
                        "--port=" + str(source_port),
                        "--user=" + source_user,
                        source_db,
                    ],
                    stdout=fout,
                    stderr=subprocess.PIPE,
                    env=dict(os.environ, MYSQL_PWD=str(source_pwd)),
                )
                # Poll so we can heartbeat the task while dump is running,
                # otherwise the task appears stuck at 30% for large DBs.
                while True:
                    try:
                        _, stderr = p.communicate(timeout=10)
                        break
                    except subprocess.TimeoutExpired:
                        try:
                            size_mb = os.path.getsize(dump_file) / 1024.0 / 1024.0
                        except (IOError, OSError) as e:
                            logger.debug("读取备份文件大小失败: %s", e)
                            size_mb = 0.0
                        self._task_step_update(
                            None, task,
                            "备份 {} 中 ({:.1f} MB)".format(source_db, size_mb),
                            dump_progress,
                        )
                if p.returncode != 0:
                    msg = (stderr or b"").decode("utf-8", errors="replace")[:500]
                    raise Exception("mysqldump 失败: " + msg)
            try:
                final_mb = os.path.getsize(dump_file) / 1024.0 / 1024.0
            except Exception:
                final_mb = 0.0
            self._append_task_log(task_id, "mysqldump 完成: {} ({:.2f} MB)".format(source_db, final_mb))
            self._append_log(source_id, "mysqldump: {} -> {} ({:.2f} MB)".format(source_db, dump_file, final_mb))

            import_progress_base = 55 + int((idx / total) * 20)
            import_progress_span = max(1, int((1.0 / total) * 20))
            self._task_step_update(None, task, "导入 {} -> {} ({}/{})".format(source_db, target_db, idx + 1, total), import_progress_base)
            self._append_task_log(task_id, "开始导入: {} -> {}（dump {:.2f} MB，大库请耐心等待）".format(source_db, target_db, final_mb))

            # import: password via MYSQL_PWD env; stdin ← dump_file
            import_started = time.time()
            last_logged_mb = -1.0
            last_logged_pct = -1
            with open(dump_file, "rb") as fin:
                p = subprocess.Popen(
                    [
                        "mysql",
                        "--host=127.0.0.1",
                        "--port=3306",
                        "--user=root",
                        target_db,
                    ],
                    stdin=fin,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=(dict(os.environ, MYSQL_PWD=str(local_root_pwd)) if local_root_pwd else dict(os.environ)),
                )
                while True:
                    try:
                        _, stderr = p.communicate(timeout=15)
                        break
                    except subprocess.TimeoutExpired:
                        # Probe target DB size to derive real progress.
                        imported_mb = 0.0
                        try:
                            rows = self._query_sql(
                                "SELECT IFNULL(SUM(data_length+index_length),0)/1024/1024 "
                                "FROM information_schema.tables WHERE table_schema='{}'".format(
                                    self._sql_escape(target_db)
                                )
                            )
                            if rows:
                                first = rows[0]
                                val = first[0] if not isinstance(first, dict) else list(first.values())[0]
                                imported_mb = float(val or 0)
                        except Exception as e:
                            logger.debug("查询目标库大小失败: %s", e)
                            imported_mb = 0.0
                        elapsed = int(time.time() - import_started)
                        # rough percent: imported DB size vs dump file size.
                        # Dumps are larger than live storage (text vs InnoDB pages),
                        # so cap at 95% to avoid premature 100%.
                        pct_inside = 0
                        if final_mb > 0:
                            pct_inside = min(95, int(imported_mb / final_mb * 100))
                        cur_progress = import_progress_base + int(import_progress_span * pct_inside / 100.0)
                        mins = elapsed // 60
                        secs = elapsed % 60
                        step_label = "导入 {} 中 {:.0f}MB/{:.0f}MB ({}%) · 已用 {}m{:02d}s".format(
                            target_db, imported_mb, final_mb, pct_inside, mins, secs,
                        )
                        self._task_step_update(None, task, step_label, cur_progress)
                        # only append to log if size or pct actually changed (anti-spam)
                        if abs(imported_mb - last_logged_mb) > 10.0 or abs(pct_inside - last_logged_pct) >= 5 or elapsed % 60 < 15:
                            self._append_task_log(task_id, "导入中 {}: {:.1f}MB / {:.1f}MB ({}%) 已用 {}s".format(
                                target_db, imported_mb, final_mb, pct_inside, elapsed,
                            ))
                            last_logged_mb = imported_mb
                            last_logged_pct = pct_inside
                if p.returncode != 0:
                    msg = (stderr or b"").decode("utf-8", errors="replace")[:500]
                    raise Exception("mysql 导入失败: " + msg)
            self._append_task_log(
                task_id,
                "导入完成: {} -> {} (耗时 {}s)".format(source_db, target_db, int(time.time() - import_started)),
            )
            idx += 1
        return True

    def recover_bootstrap_tasks(self, get=None):
        """Re-check stuck tasks and actually re-queue the recoverable ones.

        Previous behaviour only flipped status to pending without spawning
        any worker, so tasks effectively remained dormant. This version
        triggers a fresh worker (`trigger_bootstrap_task`) for every recovered
        task, so the scheduler behaves as its name suggests.
        """
        with self._with_lock():
            data = self._load_config()
            now_ts = self._now()
            recovered = []
            failed = []
            for task in data.get("bootstrap_tasks", []) or []:
                if task.get("status") != "running":
                    continue
                heartbeat = int(task.get("last_heartbeat", 0) or 0)
                if now_ts - heartbeat <= self.task_stale_timeout:
                    continue
                retry_count = int(task.get("retry_count", 0))
                max_retry = int(task.get("max_retry", 2))
                if retry_count < max_retry:
                    task["retry_count"] = retry_count + 1
                    task["status"] = "pending"
                    task["current_step"] = "任务心跳超时，已重置等待再次调度"
                    task["worker_id"] = ""
                    recovered.append(task.get("task_id"))
                else:
                    task["status"] = "failed"
                    task["error"] = "任务心跳超时且超出重试上限"
                    task["error_type"] = "任务卡死"
                    failed.append(task.get("task_id"))
                self._heartbeat_task(task)
            self._save_config(data)

        # Re-trigger outside the lock to avoid holding the file lock over shell
        triggered = []
        for tid in recovered:
            try:
                resp = self.trigger_bootstrap_task(public.to_dict_obj({"task_id": tid}))
                if resp.get("status"):
                    triggered.append(tid)
            except Exception:
                pass
        return self._ok(
            {
                "recovered_tasks": len(recovered),
                "triggered_tasks": len(triggered),
                "failed_tasks": len(failed),
                "recovered_ids": recovered,
                "failed_ids": failed,
            },
            "任务恢复完成",
            "RECOVER_OK",
        )

    def tick(self, get=None):
        """Periodic sweep entry point (called from cron via start_sync.py tick).

        Currently just invokes ``recover_bootstrap_tasks`` so that stuck tasks
        are re-queued automatically without user intervention. Safe to call
        every minute.
        """
        return self.recover_bootstrap_tasks(get)

    def check_bootstrap_tools(self, get=None):
        result = {
            "is_root": self._is_root_user(),
            "os_family": self._detect_os_family(),
            "xtrabackup": self._check_command_exists("xtrabackup"),
            "mariabackup": self._check_command_exists("mariabackup"),
            "mysqldump": self._check_command_exists("mysqldump"),
            "mysql": self._check_command_exists("mysql"),
        }
        result["physical_ready"] = result["xtrabackup"] or result["mariabackup"]
        result["logical_ready"] = result["mysqldump"] and result["mysql"]
        result["recommended_mode"] = "physical" if result["physical_ready"] else "logical"
        return public.returnMsg(True, result)

    def install_bootstrap_tool(self, get):
        if not hasattr(get, "tool_name"):
            return public.returnMsg(False, "missing parameter: tool_name")
        tool_name = str(get.tool_name).strip()
        if tool_name not in ["xtrabackup", "mariabackup"]:
            return public.returnMsg(False, "tool_name must be xtrabackup or mariabackup")

        use_retry = hasattr(get, "retry") and str(get.retry).strip().lower() in ["1", "true", "yes", "on"]
        cmd = self._build_tool_install_cmd(tool_name)
        if not cmd:
            return public.returnMsg(False, "当前系统未识别，无法自动安装，请手工安装")
        if use_retry:
            if self._detect_os_family() == "debian":
                cmd = "apt-get clean && rm -rf /var/lib/apt/lists/* && " + cmd
            else:
                cmd = "yum clean all || dnf clean all; " + cmd

        if not self._is_root_user():
            return public.returnMsg(
                False,
                {
                    "need_root": True,
                    "msg": "当前非root执行，无法自动安装",
                    "manual_cmd": cmd,
                },
            )

        install_log = os.path.join(self.log_dir, "tool_install.log")
        try:
            with open(install_log, "a") as f:
                f.write("\n======== 开始安装 {} @ {} ========\n".format(
                    tool_name, time.strftime("%Y-%m-%d %H:%M:%S")
                ))
        except (IOError, OSError) as e:
            logger.debug("写入安装日志失败: %s", e)
        # Wrap in timeout(300s) so UI never hangs if apt/yum blocks.
        exec_cmd = "timeout 600 bash -lc {} >> \"{}\" 2>&1".format(shlex.quote(cmd), install_log)
        out, err = public.ExecShell(exec_cmd)
        ok = self._check_command_exists(tool_name)
        if not ok:
            return public.returnMsg(
                False,
                {
                    "msg": "自动安装执行完成但未检测到工具，请查看安装日志",
                    "log_path": install_log,
                    "manual_cmd": cmd,
                },
            )
        return public.returnMsg(True, {"msg": "安装成功", "tool_name": tool_name, "log_path": install_log})

    def get_tool_install_command(self, get):
        if not hasattr(get, "tool_name"):
            return public.returnMsg(False, "missing parameter: tool_name")
        tool_name = str(get.tool_name).strip()
        cmd = self._build_tool_install_cmd(tool_name)
        if not cmd:
            return public.returnMsg(False, "未识别系统，无法生成安装命令")
        return public.returnMsg(True, {"tool_name": tool_name, "command": cmd})

    def get_tool_install_log(self, get=None):
        log_path = os.path.join(self.log_dir, "tool_install.log")
        if not os.path.exists(log_path):
            return self._ok({"content": ""}, "暂无安装日志", "TOOL_INSTALL_LOG_EMPTY")
        return self._ok({"content": public.ReadFile(log_path) or ""}, "读取成功", "TOOL_INSTALL_LOG_OK")

    def health_check(self, get=None):
        data = self._load_config()
        return public.returnMsg(
            True,
            {
                "plugin": "mysql_multi_source",
                "version": data.get("version", "1"),
                "mode": data.get("mode", "replica_mode"),
                "sources_count": len(data.get("sources", [])),
                "bootstrap_tasks_count": len(data.get("bootstrap_tasks", [])),
            },
        )

    def set_running_mode(self, get):
        if not hasattr(get, "mode"):
            return public.returnMsg(False, "missing parameter: mode")
        mode = str(get.mode).strip()
        if mode not in ["master_mode", "replica_mode"]:
            return public.returnMsg(False, "mode must be master_mode or replica_mode")
        data = self._load_config()
        old = data.get("mode", "replica_mode")
        data["mode"] = mode
        self._audit(data, "set_running_mode", {"old": old, "new": mode})
        self._save_config(data)
        return public.returnMsg(True, {"mode": mode})

    def get_running_mode(self, get=None):
        data = self._load_config()
        return public.returnMsg(True, {"mode": data.get("mode", "replica_mode")})

    def detect_running_mode(self, get=None):
        data = self._load_config()
        evidence = {
            "saved_mode": data.get("mode", "replica_mode"),
            "sources_count": len(data.get("sources", [])),
            "running_sources": 0,
            "master_health_ok": 0,
            "master_health_fail": 0,
            "master_health_warn": 0,
            "reason_codes": [],
        }
        master_score = 0
        replica_score = 0

        sources = data.get("sources", [])
        if len(sources) > 0:
            replica_score += 45
            evidence["reason_codes"].append("has_sources")
        for source in sources:
            status = source.get("status", {})
            if status.get("running"):
                evidence["running_sources"] += 1
        if evidence["running_sources"] > 0:
            replica_score += 20
            evidence["reason_codes"].append("has_running_sources")

        try:
            report = self.master_health_check().get("msg", {})
            summary = report.get("summary", {})
            evidence["master_health_ok"] = int(summary.get("ok", 0) or 0)
            evidence["master_health_fail"] = int(summary.get("fail", 0) or 0)
            evidence["master_health_warn"] = int(summary.get("warn", 0) or 0)
            if evidence["master_health_fail"] == 0 and evidence["master_health_ok"] >= 4:
                master_score += 45
                evidence["reason_codes"].append("master_health_good")
            elif evidence["master_health_ok"] >= 2:
                master_score += 25
                evidence["reason_codes"].append("master_health_partial")
            elif evidence["master_health_fail"] >= 3:
                replica_score += 10
                evidence["reason_codes"].append("master_health_failed")
        except Exception as ex:
            evidence["reason_codes"].append("master_health_error")
            evidence["master_health_error"] = str(ex)

        if evidence["saved_mode"] == "master_mode":
            master_score += 10
            evidence["reason_codes"].append("saved_mode_master")
        elif evidence["saved_mode"] == "replica_mode":
            replica_score += 10
            evidence["reason_codes"].append("saved_mode_replica")

        suggested_mode = "unknown"
        confidence = 50
        if master_score == replica_score:
            suggested_mode = evidence["saved_mode"] if evidence["saved_mode"] in ["master_mode", "replica_mode"] else "unknown"
            confidence = 55 if suggested_mode != "unknown" else 50
            evidence["reason_codes"].append("score_tie")
        elif master_score > replica_score:
            suggested_mode = "master_mode"
            confidence = min(100, 55 + (master_score - replica_score))
        else:
            suggested_mode = "replica_mode"
            confidence = min(100, 55 + (replica_score - master_score))

        return self._ok(
            {
                "suggested_mode": suggested_mode,
                "confidence": int(confidence),
                "scores": {
                    "master_mode": master_score,
                    "replica_mode": replica_score,
                },
                "evidence": evidence,
            },
            "身份检测完成",
            "IDENTITY_DETECTED",
        )

    def master_health_check(self, get=None):
        report = {
            "items": [],
            "summary": {"ok": 0, "warn": 0, "fail": 0},
        }

        def add_item(name, status, current, expected, suggestion):
            report["items"].append({
                "name": name,
                "status": status,
                "current": current,
                "expected": expected,
                "suggestion": suggestion,
            })
            report["summary"][status] += 1

        try:
            gtid = self._query_sql("SHOW VARIABLES LIKE 'gtid_mode'")
            gtid_value = (gtid[0][1] if gtid and not isinstance(gtid[0], dict) else (gtid[0].get("Value") if gtid else "UNKNOWN"))
            add_item("gtid_mode", "ok" if str(gtid_value).upper() == "ON" else "fail", gtid_value, "ON", "开启 gtid_mode=ON")
        except Exception as ex:
            add_item("gtid_mode", "fail", str(ex), "ON", "检查MySQL连接和权限")

        checks = [
            ("enforce_gtid_consistency", "ON"),
            ("log_bin", "ON"),
            ("binlog_format", "ROW"),
            ("server_id", ">=1"),
        ]
        for key, expected in checks:
            try:
                rows = self._query_sql("SHOW VARIABLES LIKE '{}'".format(key))
                value = rows[0][1] if rows and not isinstance(rows[0], dict) else (rows[0].get("Value") if rows else "UNKNOWN")
                if key == "server_id":
                    ok = str(value).isdigit() and int(value) > 0
                else:
                    ok = str(value).upper() == expected
                add_item(key, "ok" if ok else "fail", value, expected, "修复 {}".format(key))
            except Exception as ex:
                add_item(key, "fail", str(ex), expected, "检查变量查询权限")

        try:
            bind_rows = self._query_sql("SHOW VARIABLES LIKE 'bind_address'")
            bind_val = ""
            if bind_rows:
                bind_val = str(bind_rows[0][1] if not isinstance(bind_rows[0], dict) else bind_rows[0].get("Value", ""))
            if bind_val in ("127.0.0.1", "localhost", "::1"):
                add_item("bind_address", "fail", bind_val, "0.0.0.0 或 *",
                         "当前 MySQL 仅监听本地，从库无法连接。修改 my.cnf 中 bind-address = 0.0.0.0 并重启 MySQL")
            elif bind_val in ("0.0.0.0", "*", ""):
                add_item("bind_address", "ok", bind_val or "（默认，所有地址）", "0.0.0.0 或 *", "MySQL 监听所有网络接口")
            else:
                add_item("bind_address", "warn", bind_val, "0.0.0.0 或 *",
                         "MySQL 仅绑定 {}，请确认从库能通过此地址连接".format(bind_val))
        except Exception:
            add_item("bind_address", "warn", "无法检测", "0.0.0.0", "建议手动确认 my.cnf 中的 bind-address 设置")

        try:
            port_rows = self._query_sql("SHOW VARIABLES LIKE 'port'")
            port_val = "3306"
            if port_rows:
                port_val = str(port_rows[0][1] if not isinstance(port_rows[0], dict) else port_rows[0].get("Value", "3306"))
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(("0.0.0.0", int(port_val)))
            sock.close()
            if result == 0:
                add_item("port_listen", "ok", "端口 {} 已开放".format(port_val), "可访问",
                         "MySQL 端口正常监听中")
            else:
                add_item("port_listen", "warn", "端口 {} 连接失败".format(port_val), "可访问",
                         "请检查防火墙是否放行 {} 端口".format(port_val))
        except Exception:
            add_item("port_listen", "warn", "无法检测", "可访问", "建议手动确认端口是否开放")

        check_user = ""
        if get is not None and hasattr(get, "repl_user") and str(get.repl_user).strip():
            check_user = str(get.repl_user).strip()
        else:
            ms = self._load_config().get("master_setup")
            if ms and ms.get("repl_user"):
                check_user = str(ms["repl_user"]).strip()

        if check_user:
            try:
                safe_user = self._sql_escape(check_user)
                rows = self._query_sql(
                    "SELECT user, host, plugin FROM mysql.user WHERE user='{}'".format(safe_user)
                )
                if rows:
                    hosts_found = []
                    plugins_found = set()
                    for r in rows:
                        if isinstance(r, dict):
                            hosts_found.append(r.get("host") or r.get("Host") or "?")
                            plugins_found.add(r.get("plugin") or r.get("Plugin") or "?")
                        else:
                            hosts_found.append(str(r[1]) if len(r) > 1 else "?")
                            plugins_found.add(str(r[2]) if len(r) > 2 else "?")
                    hosts_str = ", ".join(hosts_found[:5])
                    add_item("repl_user", "ok",
                             "{}@({})".format(check_user, hosts_str), "存在",
                             "复制账号已存在")
                    bad_plugins = {"caching_sha2_password"}
                    if plugins_found & bad_plugins:
                        add_item("repl_auth_plugin", "warn",
                                 ", ".join(plugins_found), "mysql_native_password",
                                 "当前认证插件为 caching_sha2_password，MySQL 5.7 从库可能无法连接。"
                                 "建议执行: ALTER USER '{}'@'%' IDENTIFIED WITH mysql_native_password BY '密码'".format(safe_user))
                    else:
                        add_item("repl_auth_plugin", "ok",
                                 ", ".join(plugins_found), "兼容",
                                 "认证插件兼容")
                else:
                    add_item("repl_user", "warn",
                             check_user, "存在",
                             "复制账号不存在，请在下一步执行修复时创建")
            except Exception as ex:
                add_item("repl_user", "warn", str(ex), "可检查", "无法校验复制账号")
        else:
            try:
                rows = self._query_sql(
                    "SELECT user, host FROM mysql.user "
                    "WHERE Repl_slave_priv='Y' AND user NOT IN ('root','mysql.session','mysql.sys','mysql.infoschema','debian-sys-maint') "
                    "LIMIT 5"
                )
                if rows:
                    accounts = []
                    for r in rows:
                        if isinstance(r, dict):
                            accounts.append("{}@{}".format(r.get("user") or r.get("User", "?"), r.get("host") or r.get("Host", "?")))
                        else:
                            accounts.append("{}@{}".format(r[0], r[1] if len(r) > 1 else "?"))
                    add_item("repl_user", "ok",
                             "; ".join(accounts), "存在",
                             "已有复制权限的账号")
                else:
                    add_item("repl_user", "warn",
                             "未发现", "至少一个",
                             "未找到具有复制权限的账号，请在执行修复时创建")
            except Exception:
                add_item("repl_user", "warn", "无法检测", "可检查", "建议手动确认复制账号")

        return public.returnMsg(True, report)

    def master_health_report(self, get=None):
        return self.master_health_check(get)

    def master_auto_fix_preview(self, get=None):
        report = self.master_health_check().get("msg", {})
        actions = []
        need_restart = False
        for item in report.get("items", []):
            if item["status"] != "ok":
                if item["name"] in ["gtid_mode", "enforce_gtid_consistency", "log_bin", "binlog_format", "server_id"]:
                    actions.append("修改 my.cnf: {}".format(item["name"]))
                    need_restart = True
                elif item["name"] == "bind_address":
                    actions.append("修改 my.cnf: bind-address = 0.0.0.0（允许从库连接）")
                    need_restart = True
                elif item["name"] == "physical_tool":
                    actions.append("可选安装物理工具")
        return public.returnMsg(True, {"actions": actions, "need_restart": need_restart})

    def _detect_mycnf_include_dir(self):
        """探测多源复制独立配置文件的存放目录。

        优先级：
          1. CentOS/RHEL: /etc/my.cnf.d/
          2. Ubuntu/Debian: /etc/mysql/conf.d/
          3. 通用 fallback: 与 my.cnf 同目录下的 conf.d/ 子目录

        Returns:
            str: 目录路径（不保证已存在）
        """
        candidates = [
            "/etc/my.cnf.d",
            "/etc/mysql/conf.d",
        ]
        for d in candidates:
            if os.path.isdir(d):
                return d
        # fallback: 与 my.cnf 同目录
        cnf_dir = os.path.dirname(self.mysql_cnf_path) or "/etc"
        return os.path.join(cnf_dir, "conf.d")

    def _apply_master_mycnf_fix(self):
        """修复 MySQL 配置，将多源复制相关设置写入独立 include 文件。

        多源复制配置（gtid_mode / enforce_gtid_consistency / log_bin /
        binlog_format）写入独立的 multi_source.cnf，通过 !include 指令
        引入主 my.cnf。这样宝塔面板操作 my.cnf 时不会覆盖插件配置。

        首次执行时会将现有配置从 my.cnf 迁移到独立文件。
        """
        content = public.ReadFile(self.mysql_cnf_path) or ""
        if "[mysqld]" not in content:
            content += "\n[mysqld]\n"

        # --- 1. 探测 include 目录并写入独立配置文件 ---
        include_dir = self._detect_mycnf_include_dir()
        include_file = os.path.join(include_dir, "multi_source.cnf")
        include_abs = os.path.abspath(include_file)

        # 写入前备份原配置（仅首次）
        backup_path = self.mysql_cnf_path + ".bak.ms_multi"
        if not os.path.exists(backup_path):
            try:
                import shutil
                shutil.copy2(self.mysql_cnf_path, backup_path)
            except (IOError, OSError) as e:
                logger.warning("备份 my.cnf 失败: %s", e)

        # 创建目录（如不存在）
        try:
            os.makedirs(include_dir, exist_ok=True)
        except OSError as e:
            logger.debug("创建目录 %s 失败，回退到默认路径: %s", include_dir, e)
            # 目录创建失败，fallback 到 my.cnf 同目录
            include_dir = os.path.dirname(self.mysql_cnf_path) or "/etc"
            include_file = os.path.join(include_dir, "multi_source.cnf")
            include_abs = os.path.abspath(include_file)
            try:
                os.makedirs(include_dir, exist_ok=True)
            except Exception:
                pass

        # 构造独立配置文件内容
        repl_settings = (
            "# mysql_multi_source 插件自动生成，请勿手动编辑\n"
            "# 此文件通过 !include 指令引入主 my.cnf\n"
            "[mysqld]\n"
            "gtid_mode=ON\n"
            "enforce_gtid_consistency=ON\n"
            "log_bin=ON\n"
            "binlog_format=ROW\n"
        )
        public.WriteFile(include_file, repl_settings)

        # --- 2. 确保主 my.cnf 中存在 !include 指令 ---
        include_directive = "!include {}".format(include_abs)
        if include_directive not in content:
            if "[mysqld]" in content:
                content = content.replace(
                    "[mysqld]",
                    "{}\n[mysqld]".format(include_directive),
                )
            else:
                content = include_directive + "\n" + content

        # --- 3. 从 my.cnf 迁移：移除已迁移到独立文件的配置项 ---
        migrated_keys = ["gtid_mode", "enforce_gtid_consistency", "log_bin", "binlog_format"]
        updated = content
        for k in migrated_keys:
            updated = re.sub(r"(?m)^{}\s*=.*\n?".format(re.escape(k)), "", updated)

        # --- 4. 处理 server_id 和 bind-address（仍保留在 my.cnf 中） ---
        if not re.search(r"(?m)^server_id\s*=", updated):
            updated = updated.replace("[mysqld]", "[mysqld]\nserver_id={}".format(int(time.time()) % 100000 + 100))
        bind_pattern = r"(?m)^bind[-_]address\s*=\s*(.*)$"
        bind_match = re.search(bind_pattern, updated)
        if bind_match:
            cur_bind = bind_match.group(1).strip()
            if cur_bind in ("127.0.0.1", "localhost", "::1"):
                updated = re.sub(bind_pattern, "bind-address=0.0.0.0", updated)

        # --- 5. 写回 my.cnf（如有变更） ---
        if updated != content:
            public.WriteFile(self.mysql_cnf_path, updated)
            return True
        elif include_directive not in (public.ReadFile(self.mysql_cnf_path) or ""):
            # include 指令是新添加的
            public.WriteFile(self.mysql_cnf_path, updated)
            return True
        return False

    def master_auto_fix_apply(self, get=None):
        try:
            data = self._load_config()
            snap = self._create_snapshot(data, "master_auto_fix", {"my_cnf": public.ReadFile(self.mysql_cnf_path) or ""})
            changed = self._apply_master_mycnf_fix()
            auto_restart = False
            if get is not None and hasattr(get, "auto_restart"):
                auto_restart = str(get.auto_restart).strip().lower() in ["1", "true", "yes", "on"]

            restart_result = None
            if changed and auto_restart:
                out, err = public.ExecShell("/etc/init.d/mysqld restart || systemctl restart mysqld || systemctl restart mysql")
                restart_ok = not bool((err or "").strip())
                restart_result = {
                    "ok": restart_ok,
                    "err": (err or "").strip()[:500],
                }

            repl_user_result = None
            if get is not None and hasattr(get, "repl_user") and hasattr(get, "repl_password"):
                _user = str(getattr(get, "repl_user", "")).strip()
                _pwd = str(getattr(get, "repl_password", "")).strip()
                if _user and _pwd:
                    repl_host = str(getattr(get, "replica_host", "%")).strip() or "%"
                    class _g:
                        pass
                    rg = _g()
                    rg.repl_user = _user
                    rg.repl_password = _pwd
                    rg.replica_host = repl_host
                    repl_user_result = self.master_create_repl_user(rg)

            _repl_user_name = ""
            if repl_user_result and isinstance(repl_user_result, dict) and repl_user_result.get("status"):
                _repl_user_name = str(getattr(get, "repl_user", "")).strip() if get else ""

            data["master_setup"] = {
                "configured_at": self._now(),
                "snapshot_id": snap["snapshot_id"],
                "repl_user": _repl_user_name,
            }
            data["mode"] = "master_mode"

            self._audit(
                data,
                "master_auto_fix_apply",
                {
                    "changed": changed,
                    "snapshot_id": snap["snapshot_id"],
                    "auto_restart": auto_restart,
                    "restart_result": restart_result,
                    "repl_user_result": repl_user_result.get("msg") if isinstance(repl_user_result, dict) else None,
                },
            )
            self._save_config(data)
            return self._ok(
                {
                    "changed": changed,
                    "snapshot_id": snap["snapshot_id"],
                    "need_restart": changed,
                    "auto_restart": auto_restart,
                    "restart_result": restart_result,
                    "repl_user_result": repl_user_result.get("msg") if isinstance(repl_user_result, dict) else None,
                },
                "修复完成",
                "FIX_APPLIED",
            )
        except Exception as e:
            return self._fail("修复执行异常: {}".format(str(e)), "ERR_FIX_APPLY")

    def master_restart_mysql(self, get=None):
        data = self._load_config()
        out, err = public.ExecShell("/etc/init.d/mysqld restart || systemctl restart mysqld || systemctl restart mysql")
        ok = not bool((err or "").strip())
        self._audit(data, "master_restart_mysql", {"ok": ok, "err": (err or "").strip()[:500]})
        self._save_config(data)
        if not ok:
            return self._fail("重启失败: {}".format(err), "ERR_MYSQL_RESTART")
        return self._ok({"restarted": True}, "重启成功", "MYSQL_RESTARTED")

    def master_create_repl_user(self, get):
        _param_names = {"repl_user": "复制账号名", "repl_password": "复制密码", "replica_host": "允许连接的主机"}
        required = ["repl_user", "repl_password", "replica_host"]
        for k in required:
            if not hasattr(get, k) or not str(get.__getattribute__(k)).strip():
                return self._fail("缺少参数: {}".format(_param_names.get(k, k)), "ERR_PARAM_REQUIRED")
        raw_user = str(get.repl_user).strip()
        raw_pwd = str(get.repl_password)
        raw_host = str(get.replica_host).strip()

        if not re.match(r"^[A-Za-z0-9_]{1,32}$", raw_user):
            return self._fail("账号名格式不正确：仅允许字母、数字、下划线，最长32位", "ERR_PARAM_INVALID")
        if not re.match(r"^[A-Za-z0-9_.%\-]{1,60}$", raw_host):
            return self._fail("主机地址格式不正确", "ERR_PARAM_INVALID")

        safe_user = self._sql_escape(raw_user)
        safe_host = self._sql_escape(raw_host)
        safe_pwd = "'" + self._sql_escape(raw_pwd) + "'"
        try:
            exists_rows = self._query_sql(
                "SELECT COUNT(*) FROM mysql.user WHERE user='{}' AND host='{}'".format(safe_user, safe_host)
            )
            user_exists = False
            if exists_rows:
                cnt = int(exists_rows[0][0]) if not isinstance(exists_rows[0], dict) else int(exists_rows[0].get("COUNT(*)", 0))
                user_exists = cnt > 0

            if user_exists:
                try:
                    self._exec_sql("ALTER USER '{}'@'{}' IDENTIFIED BY {}".format(safe_user, safe_host, safe_pwd))
                except Exception:
                    self._exec_sql("SET PASSWORD FOR '{}'@'{}' = PASSWORD({})".format(safe_user, safe_host, safe_pwd))
            else:
                self._exec_sql("CREATE USER '{}'@'{}' IDENTIFIED BY {}".format(safe_user, safe_host, safe_pwd))
            try:
                self._exec_sql("GRANT REPLICATION REPLICA, REPLICATION CLIENT ON *.* TO '{}'@'{}'".format(safe_user, safe_host))
            except Exception:
                self._exec_sql("GRANT REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO '{}'@'{}'".format(safe_user, safe_host))
            try:
                self._exec_sql("GRANT SELECT ON *.* TO '{}'@'{}'".format(safe_user, safe_host))
            except Exception:
                pass
            for extra in (
                "GRANT RELOAD ON *.* TO '{}'@'{}'",
                "GRANT PROCESS ON *.* TO '{}'@'{}'",
                "GRANT LOCK TABLES ON *.* TO '{}'@'{}'",
                "GRANT SHOW VIEW ON *.* TO '{}'@'{}'",
                "GRANT EVENT ON *.* TO '{}'@'{}'",
                "GRANT TRIGGER ON *.* TO '{}'@'{}'",
            ):
                try:
                    self._exec_sql(extra.format(safe_user, safe_host))
                except Exception:
                    pass
            self._exec_sql("FLUSH PRIVILEGES")
            data = self._load_config()
            self._audit(data, "master_create_repl_user", {"user": raw_user, "host": raw_host})
            self._save_config(data)
            return self._ok({"user": raw_user, "host": raw_host}, "复制账号创建成功", "MASTER_REPL_USER_CREATED")
        except Exception as ex:
            return self._fail("创建复制账号失败: {}".format(ex), "ERR_MASTER_CREATE_USER")

    # ---------- Physical-mode SSH provisioning ----------

    def replica_generate_ssh_key(self, get=None):
        """Generate SSH keypair on this (replica) host if missing, and return public key.

        Uses ed25519, writes to /root/.ssh/id_ed25519. Safe to call repeatedly —
        if a keypair already exists we just read it back.
        """
        ssh_dir = "/root/.ssh"
        key_path = os.path.join(ssh_dir, "id_ed25519")
        pub_path = key_path + ".pub"
        try:
            if not os.path.isdir(ssh_dir):
                os.makedirs(ssh_dir)
            try:
                os.chmod(ssh_dir, 0o700)
            except Exception:
                pass
            if not os.path.exists(pub_path) or not os.path.exists(key_path):
                if not self._check_command_exists("ssh-keygen"):
                    return self._fail("系统未安装 ssh-keygen，请先安装 openssh-client", "ERR_SSHKEYGEN_MISSING")
                r = self._run_shell(
                    ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key_path, "-q", "-C", "mysql_multi_source"],
                    timeout=30,
                )
                if r["code"] != 0:
                    return self._fail(
                        "生成 SSH 密钥失败: " + (r.get("stderr") or r.get("stdout") or "")[:200],
                        "ERR_SSH_KEYGEN",
                    )
                try:
                    os.chmod(key_path, 0o600)
                    os.chmod(pub_path, 0o644)
                except Exception:
                    pass
            with open(pub_path, "r") as f:
                pub_key = f.read().strip()
            if not pub_key:
                return self._fail("公钥为空，建议删除 /root/.ssh/id_ed25519 重试", "ERR_SSH_EMPTY")
            return self._ok(
                {"pub_key": pub_key, "key_path": key_path, "pub_path": pub_path},
                "公钥已就绪，可复制到主库",
                "REPLICA_SSH_KEY_READY",
            )
        except Exception as ex:
            return self._fail("生成/读取公钥失败: {}".format(ex), "ERR_SSH_GENERATE")

    def master_install_replica_pubkey(self, get):
        """Install a replica's SSH public key into the master's authorized_keys.

        Idempotent: if the exact key line already exists we return without changes.
        Requires the plugin to run as root (standard on BaoTa).
        """
        if not hasattr(get, "pub_key"):
            return self._fail("缺少参数: pub_key", "ERR_PARAM_REQUIRED")
        pub_key = str(get.pub_key).strip()
        if not pub_key:
            return self._fail("公钥不能为空", "ERR_PARAM_INVALID")
        allowed_prefix = ("ssh-rsa ", "ssh-ed25519 ", "ssh-ecdsa ", "ecdsa-sha2-nistp256 ", "ecdsa-sha2-nistp384 ", "ecdsa-sha2-nistp521 ")
        if not pub_key.startswith(allowed_prefix):
            return self._fail("公钥格式不正确（需以 ssh-ed25519 / ssh-rsa / ecdsa 开头）", "ERR_PARAM_INVALID")
        # Sanity: single line, not too long
        if "\n" in pub_key or len(pub_key) > 8192:
            # Allow trailing whitespace only — strip internal newlines
            pub_key = pub_key.replace("\r", "").strip()
            if "\n" in pub_key:
                return self._fail("公钥必须是单行", "ERR_PARAM_INVALID")

        if not self._is_root_user():
            return self._fail("当前进程不是 root，无法写入 /root/.ssh/authorized_keys", "ERR_NOT_ROOT")

        ssh_dir = "/root/.ssh"
        auth_file = os.path.join(ssh_dir, "authorized_keys")
        try:
            if not os.path.isdir(ssh_dir):
                os.makedirs(ssh_dir)
            try:
                os.chmod(ssh_dir, 0o700)
            except Exception:
                pass
            existing = ""
            if os.path.exists(auth_file):
                try:
                    with open(auth_file, "r") as f:
                        existing = f.read()
                except Exception:
                    existing = ""
            already = False
            if existing:
                for line in existing.splitlines():
                    if line.strip() == pub_key:
                        already = True
                        break
            if already:
                data = self._load_config()
                self._audit(data, "master_install_replica_pubkey", {"status": "already_installed"})
                self._save_config(data)
                return self._ok({"already_installed": True}, "该公钥已存在，无需重复安装", "MASTER_PUBKEY_EXISTS")

            sep = "" if (not existing or existing.endswith("\n")) else "\n"
            with open(auth_file, "a") as f:
                f.write(sep + pub_key + "\n")
            try:
                os.chmod(auth_file, 0o600)
            except Exception:
                pass
            data = self._load_config()
            self._audit(data, "master_install_replica_pubkey", {"status": "installed"})
            self._save_config(data)
            return self._ok({"installed": True, "auth_file": auth_file}, "公钥已安装到主库 authorized_keys", "MASTER_PUBKEY_INSTALLED")
        except Exception as ex:
            return self._fail("写入 authorized_keys 失败: {}".format(ex), "ERR_PUBKEY_INSTALL")

    def master_list_replica_pubkeys(self, get=None):
        """List keys currently authorized in /root/.ssh/authorized_keys (best-effort)."""
        auth_file = "/root/.ssh/authorized_keys"
        if not os.path.exists(auth_file):
            return self._ok({"keys": []}, "暂无 authorized_keys")
        try:
            with open(auth_file, "r") as f:
                lines = f.read().splitlines()
        except Exception as ex:
            return self._fail("读取 authorized_keys 失败: {}".format(ex), "ERR_READ_AUTH_KEYS")
        keys = []
        for line in lines:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            if len(parts) >= 2:
                fp = hashlib.sha256(parts[1].encode("utf-8")).hexdigest()[:16]
                comment = parts[2] if len(parts) >= 3 else ""
                keys.append({"type": parts[0], "fingerprint": fp, "comment": comment})
        return self._ok({"keys": keys}, "已列出")

    def replica_export_handshake(self, get=None):
        """Produce a base64 handshake blob bundling this replica's SSH pubkey
        plus identifying metadata, so the user can paste it into the master
        plugin's "物理模式" card instead of raw pubkey text.

        Payload schema:
            {"type": "mms.handshake.v1",
             "source_id": optional, carried through from the most recent
                          imported profile (for audit),
             "replica_ip": best-effort detected outbound IP,
             "replica_hostname": gethostname(),
             "pub_key": "ssh-ed25519 ...",
             "created_at": ts, "expires_at": ts+24h}
        """
        key_res = self.replica_generate_ssh_key()
        if not self._is_ok_result(key_res):
            return key_res
        pub_key = (key_res.get("msg") or {}).get("pub_key", "") if isinstance(key_res, dict) else ""
        if not pub_key:
            return self._fail("获取公钥失败", "ERR_SSH_EMPTY")
        data = self._load_config()
        last_sid = ""
        try:
            srcs = data.get("sources", []) or []
            if srcs:
                last_sid = srcs[0].get("source_id", "") or ""
        except Exception:
            pass
        try:
            import socket as _socket
            hostname = _socket.gethostname()
        except Exception:
            hostname = ""
        replica_ip = ""
        try:
            import socket as _sock
            s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
            s.settimeout(1.5)
            s.connect(("8.8.8.8", 80))
            replica_ip = s.getsockname()[0]
            s.close()
        except Exception:
            try:
                import socket as _sock
                replica_ip = _sock.gethostbyname(_sock.gethostname())
            except Exception:
                replica_ip = ""
        payload = {
            "type": "mms.handshake.v1",
            "source_id": last_sid,
            "replica_ip": replica_ip,
            "replica_hostname": hostname,
            "pub_key": pub_key,
            "created_at": self._now(),
            "expires_at": self._now() + 86400,
        }
        blob = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("utf-8")
        self._audit(data, "replica_export_handshake", {"source_id": last_sid, "len": len(blob)})
        self._save_config(data)
        return self._ok(
            {"handshake_b64": blob, "pub_key": pub_key, "payload": payload},
            "握手单已生成，请复制到主库'物理模式·粘贴握手单'",
            "REPLICA_HANDSHAKE_EXPORTED",
        )

    def master_import_handshake(self, get):
        """Accept either a raw ssh-* pubkey line OR a handshake blob produced by
        replica_export_handshake. Installs the pubkey into authorized_keys.
        """
        if not hasattr(get, "payload"):
            return self._fail("缺少参数: payload", "ERR_PARAM_REQUIRED")
        raw = str(get.payload).strip()
        if not raw:
            return self._fail("内容不能为空", "ERR_PARAM_INVALID")
        pub_key = ""
        meta = {}
        # First try base64 handshake; fall back to raw pubkey line.
        parsed = None
        try:
            decoded = base64.b64decode(raw.encode("utf-8"), validate=False).decode("utf-8")
            obj = json.loads(decoded)
            if isinstance(obj, dict) and obj.get("type") == "mms.handshake.v1":
                parsed = obj
        except Exception:
            parsed = None
        if parsed:
            pub_key = str(parsed.get("pub_key", "")).strip()
            exp = int(parsed.get("expires_at", 0) or 0)
            if exp and exp < self._now():
                return self._fail("握手单已过期，请在从库重新生成", "ERR_HANDSHAKE_EXPIRED")
            meta = {
                "source_id": parsed.get("source_id", ""),
                "replica_ip": parsed.get("replica_ip", ""),
                "replica_hostname": parsed.get("replica_hostname", ""),
            }
        else:
            pub_key = raw
        if not pub_key:
            return self._fail("未解析到公钥内容", "ERR_PARAM_INVALID")
        # Delegate to the existing installer for validation + idempotent write.
        res = self.master_install_replica_pubkey(public.to_dict_obj({"pub_key": pub_key}))
        if isinstance(res, dict) and self._is_ok_result(res):
            body = res.get("msg", {}) if isinstance(res.get("msg"), dict) else {}
            body.update({"from_handshake": bool(parsed), "meta": meta})
            if parsed:
                try:
                    data = self._load_config()
                    self._audit(data, "master_import_handshake", meta)
                    self._save_config(data)
                except Exception:
                    pass
                return self._ok(body, "握手单已安装，物理模式已开通", "MASTER_HANDSHAKE_INSTALLED")
        return res

    def _is_ok_result(self, res):
        try:
            return bool(res.get("status")) if isinstance(res, dict) else False
        except Exception:
            return False

    def replica_test_ssh_to_master(self, get):
        """Quick SSH reachability probe from this (replica) host to the master.

        Capped at ~8 seconds wall time so the UI never appears to hang:
        ConnectTimeout=5 for TCP + up to 3s for auth. Disables known_hosts
        mutation to avoid prompts / stalls on first connection.
        """
        if not hasattr(get, "master_host"):
            return self._fail("缺少参数: master_host", "ERR_PARAM_REQUIRED")
        master_host = str(get.master_host).strip()
        if not master_host:
            return self._fail("master_host 不能为空", "ERR_PARAM_INVALID")
        ssh_user = str(getattr(get, "ssh_user", "root") or "root").strip() or "root"

        # Pre-flight: raw TCP probe. If port 22 is closed, fail in 2s instead
        # of waiting for ssh to time out.
        try:
            import socket as _sock
            s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
            s.settimeout(2.0)
            try:
                s.connect((master_host, 22))
                s.close()
            except Exception as ex:
                try: s.close()
                except Exception: pass
                return self._fail(
                    "SSH 测试失败：无法连接 {}:22（{}）。请检查主库 22 端口是否开放、防火墙规则。".format(master_host, ex),
                    "ERR_SSH_TCP",
                )
        except Exception:
            pass

        r = self._run_shell(
            [
                "ssh",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "GlobalKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR",
                "-o", "ConnectTimeout=5",
                "-o", "ServerAliveInterval=2",
                "-o", "ServerAliveCountMax=2",
                "-o", "PreferredAuthentications=publickey",
                "-o", "NumberOfPasswordPrompts=0",
                "{}@{}".format(ssh_user, master_host),
                "echo mms_ssh_ok",
            ],
            timeout=10,
        )
        if r["code"] == 0 and "mms_ssh_ok" in (r.get("stdout") or ""):
            return self._ok({"reachable": True}, "SSH 免密连通正常，物理模式可用")
        err = (r.get("stderr") or r.get("stdout") or "").strip()[:300] or "无错误输出"
        hint = ""
        lower = err.lower()
        if "permission denied" in lower or "publickey" in lower:
            hint = "公钥未被主库接受。请到主库\"物理模式 · 粘贴握手单\"把握手单粘贴进去，点击安装后再测试。"
        elif "timed out" in lower or "connection refused" in lower or "no route" in lower:
            hint = "SSH 网络不通，请检查主库 22 端口开放与防火墙。"
        elif "timeout" in lower:
            hint = "SSH 握手超时，可能是防火墙半连接或目标主机繁忙。"
        return self._fail("SSH 测试失败: {} {}".format(err, hint).strip(), "ERR_SSH_TEST")

    def master_list_accounts(self, get=None):
        limit = 200
        if get is not None and hasattr(get, "limit") and str(get.limit).strip().isdigit():
            limit = int(get.limit)
            if limit < 1:
                limit = 1
            if limit > 1000:
                limit = 1000
        try:
            rows = self._query_sql(
                "SELECT user, host FROM mysql.user "
                "WHERE user IS NOT NULL AND user <> '' "
                "ORDER BY user ASC, host ASC LIMIT {}".format(limit)
            ) or []
            result = []
            for row in rows:
                if isinstance(row, dict):
                    user = row.get("user", "")
                    host = row.get("host", "")
                else:
                    user = row[0] if len(row) > 0 else ""
                    host = row[1] if len(row) > 1 else ""
                user_txt = str(user or "")
                host_txt = str(host or "")
                if not user_txt:
                    continue
                grants = []
                try:
                    safe_user = self._sql_escape(user_txt)
                    safe_host = self._sql_escape(host_txt)
                    grant_rows = self._query_sql("SHOW GRANTS FOR '{}'@'{}'".format(safe_user, safe_host)) or []
                    for g in grant_rows:
                        if isinstance(g, dict):
                            first_key = list(g.keys())[0] if g else ""
                            if first_key:
                                grants.append(str(g.get(first_key, "")))
                        elif isinstance(g, (list, tuple)) and len(g) > 0:
                            grants.append(str(g[0]))
                except Exception:
                    grants = []
                result.append({
                    "user": user_txt,
                    "host": host_txt,
                    "grants": grants,
                })
            return self._ok({"accounts": result}, "账号列表获取成功", "MASTER_ACCOUNTS_LISTED")
        except Exception as ex:
            return self._fail("获取账号列表失败: {}".format(ex), "ERR_MASTER_ACCOUNTS_LIST")

    def master_update_account_password(self, get):
        required = ["account_user", "account_host", "new_password"]
        for k in required:
            if not hasattr(get, k) or not str(get.__getattribute__(k)).strip():
                return self._fail("缺少参数: {}".format(k), "ERR_PARAM")
        user = str(get.account_user).strip()
        host = str(get.account_host).strip()
        new_password = str(get.new_password).strip()
        try:
            safe_user = self._sql_escape(user)
            safe_host = self._sql_escape(host)
            safe_pwd = self._sql_escape(new_password)
            self._exec_sql("ALTER USER '{}'@'{}' IDENTIFIED BY '{}'".format(safe_user, safe_host, safe_pwd))
            self._exec_sql("FLUSH PRIVILEGES")
            data = self._load_config()
            self._audit(data, "master_update_account_password", {"user": user, "host": host})
            self._save_config(data)
            return self._ok({}, "账号密码更新成功", "MASTER_ACCOUNT_PASSWORD_UPDATED")
        except Exception as ex:
            return self._fail("账号密码更新失败: {}".format(ex), "ERR_MASTER_ACCOUNT_PASSWORD")

    def master_grant_account_privileges(self, get):
        required = ["account_user", "account_host", "privileges"]
        for k in required:
            if not hasattr(get, k) or not str(get.__getattribute__(k)).strip():
                return self._fail("缺少参数: {}".format(k), "ERR_PARAM")
        user = str(get.account_user).strip()
        host = str(get.account_host).strip()
        privileges = str(get.privileges).strip().upper()
        db_name = str(get.db_name).strip() if hasattr(get, "db_name") and str(get.db_name).strip() else "*"
        table_name = str(get.table_name).strip() if hasattr(get, "table_name") and str(get.table_name).strip() else "*"
        if not self._validate_privileges_text(privileges):
            return self._fail("privileges 格式非法，仅支持字母、下划线、逗号与空格", "ERR_PRIVILEGES_FORMAT")
        if db_name != "*" and not self._validate_mysql_scope_name(db_name):
            return self._fail("db_name 格式非法", "ERR_DB_NAME_FORMAT")
        if table_name != "*" and not self._validate_mysql_scope_name(table_name):
            return self._fail("table_name 格式非法", "ERR_TABLE_NAME_FORMAT")
        scope_db = "*" if db_name == "*" else "`{}`".format(db_name)
        scope_table = "*" if table_name == "*" else "`{}`".format(table_name)
        try:
            safe_user = self._sql_escape(user)
            safe_host = self._sql_escape(host)
            grant_sql = "GRANT {} ON {}.{} TO '{}'@'{}'".format(privileges, scope_db, scope_table, safe_user, safe_host)
            self._exec_sql(grant_sql)
            self._exec_sql("FLUSH PRIVILEGES")
            data = self._load_config()
            self._audit(data, "master_grant_account_privileges", {
                "user": user,
                "host": host,
                "privileges": privileges,
                "scope": "{}.{}".format(db_name, table_name),
            })
            self._save_config(data)
            return self._ok({}, "账号授权成功", "MASTER_ACCOUNT_PRIVILEGE_GRANTED")
        except Exception as ex:
            return self._fail("账号授权失败: {}".format(ex), "ERR_MASTER_ACCOUNT_GRANT")

    def list_change_snapshots(self, get=None):
        data = self._load_config()
        return public.returnMsg(True, data.get("change_snapshots", []))

    def rollback_snapshot(self, get):
        if not hasattr(get, "snapshot_id"):
            return public.returnMsg(False, "missing parameter: snapshot_id")
        sid = str(get.snapshot_id).strip()
        data = self._load_config()
        target = None
        for s in data.get("change_snapshots", []):
            if s.get("snapshot_id") == sid:
                target = s
                break
        if not target:
            return public.returnMsg(False, "snapshot not found")
        if target.get("category") == "master_auto_fix":
            old = target.get("payload", {}).get("my_cnf", "")
            if old:
                public.WriteFile(self.mysql_cnf_path, old)
                self._audit(data, "rollback_snapshot", {"snapshot_id": sid, "category": "master_auto_fix"})
                self._save_config(data)
                return public.returnMsg(True, "回滚成功，请重启MySQL生效")
        return public.returnMsg(False, "snapshot category not supported")

    def list_audit_logs(self, get=None):
        data = self._load_config()
        return public.returnMsg(True, data.get("audit_logs", []))

    def list_sources(self, get=None):
        data = self._load_config()
        result = []
        for source in data.get("sources", []):
            item = dict(source)
            # UI never needs the ciphertext; show mask of plaintext length only
            plain = self._crypto_decrypt(item.get("repl_password", ""))
            item["repl_password"] = self._mask_secret(plain)
            item["has_password"] = bool(plain)
            result.append(item)
        return public.returnMsg(True, result)

    def add_source(self, get):
        required = [
            "source_id",
            "channel_name",
            "master_host",
            "master_port",
            "repl_user",
            "repl_password",
        ]
        for key in required:
            if not hasattr(get, key) or not str(get.__getattribute__(key)).strip():
                return self._fail("缺少参数: {}".format(key), "ERR_PARAM_REQUIRED")
        source_id = str(get.source_id).strip()
        if not self._validate_source_id(source_id):
            return self._fail("source_id 仅支持字母、数字、下划线、中划线，最长64位", "ERR_PARAM_INVALID")
        if not self._validate_channel_name(str(get.channel_name).strip()):
            return self._fail("channel_name 仅支持字母、数字、下划线，最长64位", "ERR_PARAM_INVALID")
        try:
            master_port = int(get.master_port)
        except Exception:
            return self._fail("端口号必须为整数", "ERR_PARAM_INVALID")
        if master_port < 1 or master_port > 65535:
            return self._fail("端口号超出范围（1-65535）", "ERR_PARAM_INVALID")

        data = self._load_config()
        if self._find_source(data, source_id):
            return self._fail("该数据源ID已存在", "ERR_DUPLICATE")
        for source in data.get("sources", []):
            if source.get("channel_name") == str(get.channel_name).strip():
                return self._fail("该通道名称已存在", "ERR_DUPLICATE")

        source = {
            "source_id": source_id,
            "channel_name": str(get.channel_name).strip(),
            "master_host": str(get.master_host).strip(),
            "master_port": master_port,
            "repl_user": str(get.repl_user).strip(),
            "repl_password": self._crypto_encrypt(str(get.repl_password)),
            "sync_mode": "gtid",
            "db_mappings": [],
            "init_strategy": "auto",
            "status": {
                "running": False,
                "io_running": "No",
                "sql_running": "No",
                "seconds_behind": None,
                "last_error": "",
            },
            "created_at": self._now(),
            "updated_at": self._now(),
        }
        data["sources"].append(source)
        self._append_log(source["source_id"], "添加主库来源成功，channel={}".format(source["channel_name"]))
        self._audit(data, "add_source", {"source_id": source_id, "channel_name": source["channel_name"]})

        if not self._save_config(data):
            return self._fail("保存配置失败", "ERR_SAVE_CONFIG")
        return self._ok({"source_id": source_id}, "来源添加成功", "SOURCE_ADDED")

    def test_source_connection(self, get):
        if not hasattr(get, "source_id"):
            return public.returnMsg(False, "missing parameter: source_id")

        data = self._load_config()
        item = self._find_source(data, str(get.source_id).strip())
        if not item:
            return public.returnMsg(False, "source not found")

        host = item.get("master_host")
        port = int(item.get("master_port", 3306))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        try:
            sock.connect((host, port))
            return public.returnMsg(True, {"ok": True, "msg": "主库网络连通", "reason": "连接正常"})
        except Exception as ex:
            return public.returnMsg(False, {"ok": False, "msg": "主库网络不通: {}".format(ex), "reason": self._classify_connectivity_error(ex)})
        finally:
            sock.close()

    def test_master_connection_direct(self, get):
        required = ["master_host", "master_port", "repl_user", "repl_password"]
        for k in required:
            if not hasattr(get, k) or not str(get.__getattribute__(k)).strip():
                return self._fail("缺少参数: {}".format(k), "ERR_PARAM_REQUIRED")
        host = str(get.master_host).strip()
        try:
            port = int(get.master_port)
        except Exception:
            return self._fail("端口号必须为整数", "ERR_PARAM_INVALID")
        user = str(get.repl_user).strip()
        pwd = str(get.repl_password)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        try:
            sock.connect((host, port))
        except Exception as ex:
            sock.close()
            return self._fail(
                "网络连通失败: {}".format(ex),
                "ERR_NETWORK",
                {"ok": False, "reason": self._classify_connectivity_error(ex)},
            )
        finally:
            try:
                sock.close()
            except Exception:
                pass

        if not self._check_command_exists("mysql"):
            return self._ok(
                {"ok": True, "reason": "仅验证网络层，未检测到本地 mysql 客户端，跳过账号校验"},
                "网络已连通；未校验账号（建议安装 mysql 客户端）",
                "MASTER_CONNECT_NET_ONLY",
            )

        r = self._run_shell(
            ["mysql",
             "-h", host,
             "-P", str(port),
             "-u", user,
             "-e", "SELECT 1"],
            env_extra={"MYSQL_PWD": pwd},
            timeout=15,
        )
        if r["code"] != 0:
            err = (r.get("stderr") or "").strip() or "mysql 退出码 {}".format(r["code"])
            return self._fail(
                err,
                "ERR_MASTER_AUTH" if "access denied" in err.lower() else "ERR_MASTER_CONNECT",
                {"ok": False, "reason": self._classify_connectivity_error(err)},
            )
        return self._ok(
            {"ok": True, "reason": "可连接并可执行查询"},
            "网络与账号校验通过",
            "MASTER_CONNECT_OK",
        )

    def get_gtid_status(self, get=None):
        try:
            rows = self._query_sql("SHOW VARIABLES LIKE 'gtid_mode'")
            if not rows:
                return public.returnMsg(False, "无法读取 gtid_mode")
            value = rows[0][1] if not isinstance(rows[0], dict) else rows[0].get("Value", "")
            return public.returnMsg(True, {"gtid_mode": value, "enabled": str(value).upper() == "ON"})
        except Exception as ex:
            return public.returnMsg(False, "读取 GTID 状态失败: {}".format(ex))

    def set_db_mappings(self, get):
        if not hasattr(get, "source_id"):
            return self._fail("缺少参数: source_id", "ERR_PARAM_REQUIRED")
        if not hasattr(get, "mappings"):
            return self._fail("缺少参数: mappings", "ERR_PARAM_REQUIRED")

        data = self._load_config()
        item = self._find_source(data, str(get.source_id).strip())
        if not item:
            return self._fail("未找到该数据源", "ERR_NOT_FOUND")

        try:
            mappings = get.mappings
            if isinstance(mappings, str):
                mappings = json.loads(mappings)
            if not isinstance(mappings, list):
                return self._fail("mappings 必须为列表格式", "ERR_PARAM_INVALID")

            normalized = []
            for m in mappings:
                if not isinstance(m, dict):
                    return self._fail("映射项格式不正确", "ERR_PARAM_INVALID")
                source_db = str(m.get("source_db", "")).strip()
                target_db = str(m.get("target_db", "")).strip()
                if not source_db or not target_db:
                    return self._fail("源数据库和目标数据库不能为空", "ERR_PARAM_REQUIRED")
                normalized.append({"source_db": source_db, "target_db": target_db})
            item["db_mappings"] = normalized
            item["updated_at"] = self._now()
            self._append_log(item["source_id"], "更新库映射，共{}条".format(len(normalized)))
            self._save_config(data)
            return self._ok({"source_id": item.get("source_id"), "count": len(normalized)}, "库映射更新成功")
        except Exception as ex:
            return self._fail("映射配置解析失败: {}".format(ex), "ERR_PARSE_MAPPINGS")

    def list_db_mappings(self, get):
        if not hasattr(get, "source_id"):
            return public.returnMsg(False, "missing parameter: source_id")
        data = self._load_config()
        item = self._find_source(data, str(get.source_id).strip())
        if not item:
            return public.returnMsg(False, "source not found")
        return public.returnMsg(True, item.get("db_mappings", []))

    def check_target_db_conflicts(self, get=None):
        if get is None or not hasattr(get, "mappings"):
            return self._fail("缺少参数: mappings", "ERR_PARAM_REQUIRED")

        raw_mappings = getattr(get, "mappings", [])
        if isinstance(raw_mappings, str):
            try:
                raw_mappings = json.loads(raw_mappings) if raw_mappings.strip() else []
            except Exception:
                return self._fail("mappings 格式非法（需 JSON）", "ERR_PARAM_INVALID")
        if not isinstance(raw_mappings, list):
            return self._fail("mappings 必须为列表格式", "ERR_PARAM_INVALID")

        exclude_source_id = str(getattr(get, "exclude_source_id", "") or "").strip()
        normalized = []
        for m in raw_mappings:
            if not isinstance(m, dict):
                return self._fail("映射项格式不正确", "ERR_PARAM_INVALID")
            source_db = str(m.get("source_db", "")).strip()
            target_db = str(m.get("target_db", "")).strip()
            if not source_db or not target_db:
                return self._fail("源数据库和目标数据库不能为空", "ERR_PARAM_REQUIRED")
            if not self._validate_mysql_scope_name(source_db) or not self._validate_mysql_scope_name(target_db):
                return self._fail("库名只能含字母/数字/下划线/-/$", "ERR_PARAM_INVALID")
            normalized.append({"source_db": source_db, "target_db": target_db})

        conflicts = self._collect_target_db_conflicts(normalized, exclude_source_id=exclude_source_id)
        return self._ok(
            {
                "ok": len(conflicts) == 0,
                "conflicts": conflicts,
                "checked_count": len(normalized),
                "exclude_source_id": exclude_source_id,
            },
            "未发现目标库冲突" if not conflicts else "检测到目标库冲突，请调整目标库名",
            "TARGET_DB_CONFLICTS_OK" if not conflicts else "TARGET_DB_CONFLICTS_FOUND",
        )

    def remove_source(self, get):
        if not hasattr(get, "source_id"):
            return self._fail("缺少参数: source_id", "ERR_PARAM_REQUIRED")
        source_id = str(get.source_id).strip()

        data = self._load_config()
        item = self._find_source(data, source_id)
        old_len = len(data.get("sources", []))
        data["sources"] = [s for s in data.get("sources", []) if s.get("source_id") != source_id]
        if len(data["sources"]) == old_len:
            return self._fail("未找到该数据源", "ERR_NOT_FOUND")

        # Cancel related bootstrap tasks to avoid stale worker writes resurrecting source.
        cancelled_task_ids = []
        for t in data.get("bootstrap_tasks", []) or []:
            if t.get("source_id") != source_id:
                continue
            if t.get("status") in ("done", "failed", "cancelled"):
                continue
            t["status"] = "cancelled"
            t["current_step"] = "来源已删除，任务取消"
            t["error"] = "source removed by user"
            self._heartbeat_task(t)
            cancelled_task_ids.append(t.get("task_id"))

        # best-effort: stop channel before removal (ignore errors)
        if item and self._validate_channel_name(item.get("channel_name", "")):
            try:
                self._exec_sql(self._replication_sql("STOP", channel=self._sql_escape(item.get("channel_name"))))
            except Exception:
                pass

        if item:
            self._append_log(source_id, "删除来源")
        self._audit(data, "remove_source", {"source_id": source_id, "cancelled_tasks": cancelled_task_ids})
        if not self._save_config(data):
            return self._fail("保存配置失败", "ERR_SAVE_CONFIG")
        for tid in cancelled_task_ids:
            if tid:
                self._append_task_log(tid, "cancelled because source {} removed".format(source_id))
        return self._ok({"source_id": source_id}, "来源已删除", "SOURCE_REMOVED")

    def _auto_start_channel_after_bootstrap(self, source, task):
        """Bring replication online right after the initial data copy finishes.

        Strategy:
          1. If the task captured master's gtid_executed before mysqldump AND
             the replica has no replication state yet (fresh box), execute
             RESET MASTER + SET @@GLOBAL.gtid_purged so MASTER_AUTO_POSITION=1
             will not try to re-apply the historical rows we already dumped.
             Important: RESET MASTER is global; we only run it when gtid_executed
             is empty so we never disrupt existing multi-source channels.
          2. Always run CHANGE REPLICATION SOURCE TO + START REPLICA for the
             channel (old syntax: CHANGE MASTER TO + START SLAVE).
          3. Refresh source.status from SHOW REPLICA STATUS so the dashboard
             immediately reflects io_running / sql_running.
        """
        source_id = source.get("source_id")
        channel_name = source.get("channel_name")
        task_id = task.get("task_id")
        if not self._validate_channel_name(channel_name):
            raise Exception("通道名称无效: {}".format(channel_name))

        safe_channel = self._sql_escape(channel_name)
        master_host_s = self._sql_escape(source.get("master_host"))
        master_port = int(source.get("master_port", 3306))
        repl_user_s = self._sql_escape(source.get("repl_user"))
        plain_pwd = self._decrypted_password(source)
        repl_pwd_s = self._sql_escape(plain_pwd)

        captured_gtid = task.get("master_gtid_at_dump")
        if captured_gtid is not None and str(captured_gtid).strip():
            current_gtid = ""
            try:
                rows = self._query_sql("SELECT @@GLOBAL.gtid_executed")
                if rows:
                    first = rows[0]
                    val = first[0] if not isinstance(first, dict) else list(first.values())[0]
                    current_gtid = str(val or "").strip()
            except Exception as e:
                logger.debug("查询从库 gtid_executed 失败: %s", e)
                current_gtid = ""
            if not current_gtid:
                self._append_task_log(
                    task_id,
                    "[auto-start] 从库 gtid_executed 为空，执行 RESET MASTER + SET GTID_PURGED='{}'".format(captured_gtid),
                )
                try:
                    self._exec_sql("RESET MASTER")
                except Exception as ex:
                    self._append_task_log(task_id, "[auto-start] RESET MASTER 失败（忽略）: {}".format(ex))
                if not self._validate_gtid_set(str(captured_gtid)):
                    self._append_task_log(task_id, "[auto-start] GTID 格式校验失败，拒绝执行 SET GTID_PURGED")
                    self._append_log(source_id, "GTID 格式校验失败，疑似注入: {}".format(self._mask_secret(str(captured_gtid))))
                    raise ValueError("GTID 格式校验失败: {}".format(self._mask_secret(str(captured_gtid))))
                try:
                    self._exec_sql("SET @@GLOBAL.gtid_purged = '{}'".format(self._sql_escape(str(captured_gtid))))
                except Exception as ex:
                    self._append_task_log(task_id, "[auto-start] SET GTID_PURGED 失败（忽略，可能会出现重复事件）: {}".format(ex))
            else:
                self._append_task_log(
                    task_id,
                    "[auto-start] 从库已有 gtid_executed，跳过 RESET MASTER；当前: {}".format(current_gtid),
                )
        else:
            self._append_task_log(task_id, "[auto-start] 未捕获主库 GTID，跳过 GTID 对齐（可能出现重复事件）")

        try:
            self._exec_sql(self._replication_sql("STOP", channel=safe_channel))
        except Exception:
            pass

        change_sql = self._replication_sql(
            "CHANGE_MASTER", channel=safe_channel,
            host=master_host_s, port=master_port,
            user=repl_user_s, pwd=repl_pwd_s,
        )
        self._append_task_log(task_id, "[auto-start] CHANGE REPLICATION SOURCE TO ... FOR CHANNEL '{}'".format(channel_name))
        self._exec_sql(change_sql)
        self._exec_sql(self._replication_sql("START", channel=safe_channel))
        self._append_task_log(task_id, "[auto-start] START REPLICA 已发出，开始追数")

        # Refresh dashboard state from SHOW REPLICA STATUS
        try:
            status = self._get_source_status(channel_name)
            data = self._load_config()
            src_live = self._find_source(data, source_id)
            if src_live:
                src_live["status"] = status
                src_live["updated_at"] = self._now()
                self._save_config(data)
        except Exception:
            pass
        self._append_log(source_id, "bootstrap 完成后自动启动 channel")

    def start_channel(self, get):
        if not hasattr(get, "source_id"):
            return self._fail("缺少参数: source_id", "ERR_PARAM_REQUIRED")

        data = self._load_config()
        item = self._find_source(data, str(get.source_id).strip())
        if not item:
            return self._fail("未找到该数据源", "ERR_NOT_FOUND")
        channel_name = item.get("channel_name")
        if not self._validate_channel_name(channel_name):
            return self._fail("通道名称无效", "ERR_PARAM_INVALID")

        safe_channel = self._sql_escape(channel_name)
        master_host = self._sql_escape(item.get("master_host"))
        master_port = int(item.get("master_port", 3306))
        repl_user = self._sql_escape(item.get("repl_user"))
        plain_pwd = self._decrypted_password(item)
        repl_password = self._sql_escape(plain_pwd)

        try:
            self._exec_sql(self._replication_sql("STOP", channel=safe_channel))
        except Exception:
            pass

        try:
            change_sql = self._replication_sql(
                "CHANGE_MASTER", channel=safe_channel,
                host=master_host, port=master_port,
                user=repl_user, pwd=repl_password,
            )
            self._exec_sql(change_sql)
            self._exec_sql(self._replication_sql("START", channel=safe_channel))
        except Exception as ex:
            item["status"]["running"] = False
            item["status"]["io_running"] = "No"
            item["status"]["sql_running"] = "No"
            item["status"]["last_error"] = "启动失败: {}".format(ex)
            item["updated_at"] = self._now()
            self._save_config(data)
            return self._fail(item["status"]["last_error"], "ERR_START_CHANNEL")

        item["status"] = self._get_source_status(channel_name)
        item["updated_at"] = self._now()
        self._append_log(item["source_id"], "启动 channel 成功")
        self._save_config(data)
        return self._ok({"source_id": item["source_id"], "status": item["status"]}, "通道启动成功", "CHANNEL_STARTED")

    def stop_channel(self, get):
        if not hasattr(get, "source_id"):
            return public.returnMsg(False, "missing parameter: source_id")

        data = self._load_config()
        item = self._find_source(data, str(get.source_id).strip())
        if not item:
            return public.returnMsg(False, "source not found")
        channel_name = item.get("channel_name")
        if not self._validate_channel_name(channel_name):
            return public.returnMsg(False, "invalid channel_name")

        safe_channel = self._sql_escape(channel_name)
        try:
            self._exec_sql(self._replication_sql("STOP", channel=safe_channel))
        except Exception as ex:
            item["status"]["last_error"] = "停止失败: {}".format(ex)
            item["updated_at"] = self._now()
            self._save_config(data)
            return public.returnMsg(False, item["status"]["last_error"])

        item["status"] = self._get_source_status(channel_name)
        item["updated_at"] = self._now()
        self._append_log(item["source_id"], "停止 channel")
        self._save_config(data)
        return public.returnMsg(True, "通道已停止")

    def channel_status(self, get):
        if not hasattr(get, "source_id"):
            return public.returnMsg(False, "missing parameter: source_id")
        data = self._load_config()
        item = self._find_source(data, str(get.source_id).strip())
        if not item:
            return public.returnMsg(False, "source not found")
        item["status"] = self._get_source_status(item.get("channel_name"))
        item["updated_at"] = self._now()
        self._save_config(data)
        return public.returnMsg(True, item.get("status", {}))

    def create_bootstrap_task(self, get):
        if not hasattr(get, "source_id"):
            return self._fail("缺少参数: source_id", "ERR_PARAM_REQUIRED")
        mode = "auto"
        if hasattr(get, "mode") and str(get.mode).strip():
            mode = str(get.mode).strip()
        if mode not in ["auto", "physical", "logical"]:
            return self._fail("模式参数无效，仅支持 auto/physical/logical", "ERR_PARAM_INVALID")

        data = self._load_config()
        source = self._find_source(data, str(get.source_id).strip())
        if not source:
            return self._fail("未找到该数据源", "ERR_NOT_FOUND")
        if not source.get("db_mappings"):
            return self._fail("请先配置库映射后再创建初始化任务", "ERR_MAPPINGS_EMPTY")
        for task in data.get("bootstrap_tasks", []) or []:
            if task.get("source_id") != source.get("source_id"):
                continue
            if task.get("status") not in ("pending", "running"):
                continue
            return self._fail(
                "该来源已有执行中的初始化任务，请复用现有任务或等待其完成",
                "ERR_TASK_ALREADY_ACTIVE",
                {
                    "task_id": task.get("task_id", ""),
                    "status": task.get("status", ""),
                    "current_step": task.get("current_step", ""),
                },
            )

        task_id = "boot_" + uuid.uuid4().hex[:12]
        task = {
            "task_id": task_id,
            "source_id": source.get("source_id"),
            "channel_name": source.get("channel_name"),
            "mode": mode,
            "status": "pending",
            "progress": 0,
            "current_step": "等待执行",
            "steps": [
                "源库准备检查",
                "备份数据",
                "传输文件",
                "导入目标",
                "校验并接管复制",
            ],
            "error": "",
            "error_type": "",
            "retry_count": 0,
            "max_retry": 2,
            "checkpoint_step": "",
            "worker_id": "",
            "last_heartbeat": self._now(),
            "started_at": 0,
            "finished_at": 0,
            "duration_seconds": 0,
            "created_at": self._now(),
            "updated_at": self._now(),
        }
        data["bootstrap_tasks"].insert(0, task)
        self._save_config(data)
        self._append_log(source.get("source_id"), "创建初始化任务: {}, mode={}".format(task_id, mode))
        return self._ok(task, "任务创建成功")

    def run_bootstrap_task(self, get):
        if not hasattr(get, "task_id"):
            return public.returnMsg(False, "missing parameter: task_id")
        task_id = str(get.task_id).strip()
        incoming_worker = ""
        if hasattr(get, "worker_id"):
            incoming_worker = str(get.worker_id).strip()

        # Atomic CAS: only one caller can flip pending -> running. If another
        # worker is already running this task we silently bail (prevents the
        # thread-primary + subprocess-backup pair from double-executing).
        def _claim(d):
            t = self._find_bootstrap_task(d, task_id)
            if not t:
                return ("missing", None)
            st = t.get("status")
            if st == "done":
                return ("done", t)
            if st == "cancelled":
                return ("cancelled", t)
            if st == "running":
                cur = t.get("worker_id") or ""
                if cur and incoming_worker and cur != incoming_worker:
                    return ("busy", t)
                # same worker re-entering (e.g. recovery), allow.
            t["status"] = "running"
            t["progress"] = t.get("progress") or 0
            t["current_step"] = t.get("current_step") or "初始化开始"
            t["started_at"] = t.get("started_at") or self._now()
            if incoming_worker:
                t["worker_id"] = incoming_worker
            elif not t.get("worker_id"):
                t["worker_id"] = "worker_" + uuid.uuid4().hex[:8]
            t["error"] = ""
            t["error_type"] = ""
            t["updated_at"] = self._now()
            t["last_heartbeat"] = self._now()
            return ("claimed", t)
        flag, task = self._update_config(_claim)
        if flag == "missing":
            return public.returnMsg(False, "task not found")
        if flag == "done":
            return public.returnMsg(True, "任务已完成")
        if flag == "cancelled":
            return public.returnMsg(False, "任务已取消")
        if flag == "busy":
            try:
                self._append_task_log(task_id, "worker={} 未抢占到任务（已在其他worker执行）".format(incoming_worker or "-"))
            except Exception:
                pass
            return public.returnMsg(False, "任务已被其他worker接管")
        # Re-load so downstream logic has a consistent snapshot.
        data = self._load_config()
        task = self._find_bootstrap_task(data, task_id)
        if not task:
            return public.returnMsg(False, "task not found")
        self._append_task_log(task_id, "worker={} 抢占成功，开始执行".format(task.get("worker_id")))

        try:
            source = self._find_source(data, task.get("source_id"))
            if not source:
                raise Exception("source not found for task")
            step_count = len(task.get("steps", [])) or 1

            self._task_step_update(data, task, "源库准备检查", 10)
            if not source.get("db_mappings"):
                raise Exception("库映射为空")

            self._task_step_update(data, task, "备份数据", 30)
            effective_mode = self._resolve_task_mode(task.get("mode"))
            task["effective_mode"] = effective_mode
            if effective_mode == "physical":
                self._run_physical_bootstrap(source, task)
            else:
                self._run_logical_bootstrap(source, task)

            self._task_step_update(data, task, "传输文件", 55)
            time.sleep(0.2)
            self._task_step_update(data, task, "导入目标", 75)
            time.sleep(0.2)

            # --- Take over replication: set GTID if captured, then CHANGE
            # REPLICATION SOURCE TO + START REPLICA automatically so the user
            # doesn't have to click "启动通道" after the initial data copy
            # completes. -------
            self._task_step_update(data, task, "校验并接管复制", 90)
            channel_start_err = None
            try:
                data2 = self._load_config()
                task2 = self._find_bootstrap_task(data2, task_id) or task
                source2 = self._find_source(data2, task2.get("source_id"))
                if source2:
                    self._auto_start_channel_after_bootstrap(source2, task2)
                    task = task2
            except Exception as start_ex:
                channel_start_err = str(start_ex)
                self._append_task_log(task_id, "[auto-start] 启动通道失败: {}".format(channel_start_err))

            data = self._load_config()
            task = self._find_bootstrap_task(data, task_id)
            if not task:
                return public.returnMsg(False, "task not found")
            task["checkpoint_step"] = task.get("steps", [])[step_count - 1]
            task["progress"] = 100
            task["current_step"] = "初始化完成（复制已启动）" if not channel_start_err else "初始化完成（请手动启动通道）"
            task["status"] = "done"
            if channel_start_err:
                task["channel_start_error"] = channel_start_err
            task["finished_at"] = self._now()
            task["duration_seconds"] = max(0, int(task["finished_at"] - int(task.get("started_at", task["finished_at"]))))
            self._heartbeat_task(task)
            self._save_config(data)
            self._append_log(task.get("source_id"), "初始化任务完成: {}".format(task_id))
            self._append_task_log(task_id, "task done in {}s".format(task["duration_seconds"]))
            return public.returnMsg(True, task)
        except Exception as ex:
            data = self._load_config()
            task = self._find_bootstrap_task(data, task_id)
            if not task:
                return public.returnMsg(False, "task not found")
            retry_count = int(task.get("retry_count", 0))
            max_retry = int(task.get("max_retry", 2))
            task["error"] = str(ex)
            task["error_type"] = self._classify_error(ex)
            if retry_count < max_retry:
                backoff_sec = min(20, 2 ** retry_count)
                task["retry_count"] = retry_count + 1
                task["status"] = "pending"
                task["current_step"] = "失败待重试(backoff={}s)".format(backoff_sec)
            else:
                task["status"] = "failed"
                task["current_step"] = "任务失败"
                task["finished_at"] = self._now()
                task["duration_seconds"] = max(0, int(task["finished_at"] - int(task.get("started_at", task["finished_at"]))))
            self._heartbeat_task(task)
            self._save_config(data)
            self._append_log(task.get("source_id"), "初始化任务失败: {} | {}".format(task_id, ex))
            self._append_task_log(task_id, "task failed: {} ({})".format(ex, task["error_type"]))
            return public.returnMsg(False, task["error"])

    def trigger_bootstrap_task(self, get):
        if not hasattr(get, "task_id"):
            return self._fail("缺少参数: task_id", "ERR_PARAM_REQUIRED")
        task_id = str(get.task_id).strip()

        data = self._load_config()
        task = self._find_bootstrap_task(data, task_id)
        if not task:
            return self._fail("未找到该任务", "ERR_NOT_FOUND")
        if task.get("status") == "running":
            return self._fail("任务正在执行中", "ERR_TASK_RUNNING")
        if task.get("status") == "done":
            return self._fail("任务已完成，若需重跑请新建任务", "ERR_TASK_DONE")
        if task.get("status") == "cancelled":
            return self._fail("任务已取消，请新建任务后再执行", "ERR_TASK_CANCELLED")

        worker_id = "worker_" + uuid.uuid4().hex[:8]
        task["worker_id"] = worker_id
        task["last_heartbeat"] = self._now()
        task["retry_count"] = 0
        task["error"] = ""
        task["error_type"] = ""
        self._save_config(data)

        self._append_task_log(task_id, "======== 触发新一轮执行 worker={} ========".format(worker_id))
        self._append_log(task.get("source_id"), "异步触发初始化任务: {} by {}".format(task_id, worker_id))

        # ---- Primary path: in-process daemon thread (survives HTTP response,
        # always works inside BaoTa's running Flask process, no pyenv/import
        # hazards). CAS inside run_bootstrap_task prevents double-execution if
        # the subprocess backup below also claims the same worker_id.
        thread_ok = False
        def _bg_run():
            try:
                self._append_task_log(task_id, "[thread] 进入 run_bootstrap_task")
                self.run_bootstrap_task(public.to_dict_obj({"task_id": task_id, "worker_id": worker_id}))
                self._append_task_log(task_id, "[thread] run_bootstrap_task 返回")
            except Exception as ex:
                try:
                    import traceback as _tb
                    self._append_task_log(task_id, "[thread] 失败: {}\n{}".format(ex, _tb.format_exc()[:2000]))
                except Exception:
                    pass
        try:
            threading.Thread(target=_bg_run, daemon=True).start()
            thread_ok = True
            self._append_task_log(task_id, "[thread] 已启动（主执行路径）")
        except Exception as ex:
            self._append_task_log(task_id, "[thread] 启动失败: {}".format(ex))

        # ---- Backup path: detached subprocess via btpython/start_sync.py.
        # stderr/stdout captured to a per-task file so we can *see* why it died
        # instead of silent failure. If the thread already ran to completion,
        # run_bootstrap_task's CAS will turn this into a no-op cheaply.
        launcher = "/www/server/panel/plugin/mysql_multi_source/start_sync.py"
        btpython = "/www/server/panel/pyenv/bin/python"
        python_bin = btpython if os.path.exists(btpython) else (
            "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else "python3"
        )
        if not os.path.exists(launcher):
            self._append_task_log(task_id, "[subproc] 跳过: {} 不存在（仅使用线程路径）".format(launcher))
        else:
            err_dir = "/www/server/panel/plugin/mysql_multi_source"
            stderr_path = os.path.join(err_dir, "{}.stderr.log".format(task_id))
            try:
                err_fp = open(stderr_path, "ab")
                try:
                    err_fp.write("\n==== {} spawn by {} ====\n".format(time.strftime("%Y-%m-%d %H:%M:%S"), worker_id).encode("utf-8"))
                    err_fp.flush()
                except Exception:
                    pass
                subprocess.Popen(
                    [python_bin, launcher, "run_bootstrap_task", task_id, worker_id],
                    stdout=err_fp,
                    stderr=err_fp,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                )
                self._append_task_log(
                    task_id,
                    "[subproc] 已拉起（备份路径）bin={} stderr={}".format(python_bin, stderr_path),
                )
            except Exception as ex:
                self._append_task_log(task_id, "[subproc] 拉起失败: {}".format(ex))

        if not thread_ok:
            return self._fail("任务触发失败：线程和子进程均未能启动", "ERR_TRIGGER_FAIL")
        return self._ok({"task_id": task_id, "worker_id": worker_id}, "已触发后台执行")

    def get_bootstrap_tasks(self, get=None):
        data = self._load_config()
        return public.returnMsg(True, data.get("bootstrap_tasks", []))

    def run_stress_wizard(self, get):
        source_count = int(get.source_count) if hasattr(get, "source_count") and str(get.source_count).isdigit() else 1
        task_per_source = int(get.task_per_source) if hasattr(get, "task_per_source") and str(get.task_per_source).isdigit() else 1
        mode = str(get.mode).strip() if hasattr(get, "mode") and str(get.mode).strip() else "auto"
        data = self._load_config()
        sources = data.get("sources", [])[:source_count]
        if not sources:
            return public.returnMsg(False, "没有可用于压测的来源")
        created = 0
        failed = 0
        task_ids = []
        for source in sources:
            sid = source.get("source_id")
            for _ in range(task_per_source):
                resp = self.create_bootstrap_task(public.to_dict_obj({"source_id": sid, "mode": mode}))
                if resp.get("status"):
                    created += 1
                    task = resp.get("msg", {})
                    tid = task.get("task_id")
                    task_ids.append(tid)
                    self.trigger_bootstrap_task(public.to_dict_obj({"task_id": tid}))
                else:
                    failed += 1
        return public.returnMsg(True, {"created": created, "failed": failed, "task_ids": task_ids[:100]})

    def get_stress_report(self, get=None):
        data = self._load_config()
        tasks = data.get("bootstrap_tasks", [])
        total = len(tasks)
        done = len([t for t in tasks if t.get("status") == "done"])
        failed = len([t for t in tasks if t.get("status") == "failed"])
        running = len([t for t in tasks if t.get("status") == "running"])
        avg_duration = 0
        done_tasks = [t for t in tasks if t.get("status") == "done" and int(t.get("duration_seconds", 0) or 0) > 0]
        if done_tasks:
            avg_duration = int(sum([int(t.get("duration_seconds", 0) or 0) for t in done_tasks]) / len(done_tasks))
        fail_types = {}
        for t in tasks:
            et = t.get("error_type")
            if et:
                fail_types[et] = fail_types.get(et, 0) + 1
        success_rate = 0
        if total > 0:
            success_rate = round(float(done) / float(total), 4)
        return public.returnMsg(True, {"total_tasks": total, "done": done, "failed": failed, "running": running, "avg_duration_seconds": avg_duration, "success_rate": success_rate, "fail_types": fail_types})

    def get_bootstrap_task(self, get):
        if not hasattr(get, "task_id"):
            return public.returnMsg(False, "missing parameter: task_id")
        task_id = str(get.task_id).strip()
        data = self._load_config()
        task = self._find_bootstrap_task(data, task_id)
        if not task:
            return public.returnMsg(False, "task not found")
        return public.returnMsg(True, task)

    def cancel_bootstrap_task(self, get):
        if not hasattr(get, "task_id"):
            return public.returnMsg(False, "missing parameter: task_id")
        task_id = str(get.task_id).strip()
        data = self._load_config()
        for task in data.get("bootstrap_tasks", []):
            if task.get("task_id") == task_id:
                if task.get("status") == "done":
                    return public.returnMsg(False, "已完成任务不能取消")
                task["status"] = "cancelled"
                task["current_step"] = "用户取消"
                task["updated_at"] = self._now()
                self._save_config(data)
                self._append_log(task.get("source_id"), "取消初始化任务: {}".format(task_id))
                return public.returnMsg(True, "任务已取消")
        return public.returnMsg(False, "task not found")

    # =======================================================================
    # Wizard / orchestrator APIs
    # =======================================================================

    def wizard_detect_env(self, get=None):
        """Aggregate identity, tools, GTID and summary counts for landing."""
        data = self._load_config()
        sources = data.get("sources", []) or []
        tasks = data.get("bootstrap_tasks", []) or []
        running_sources = 0
        try:
            status_map = self._all_slave_status()
            for s in sources:
                row = status_map.get(s.get("channel_name") or "", None)
                if self._map_status_row(row or {}).get("running"):
                    running_sources += 1
        except Exception:
            pass

        try:
            tools_resp = self.check_bootstrap_tools()
            tools = tools_resp.get("msg", {}) if isinstance(tools_resp, dict) else {}
        except Exception:
            tools = {}

        gtid_enabled = False
        gtid_value = ""
        try:
            rows = self._query_sql("SHOW VARIABLES LIKE 'gtid_mode'") or []
            if rows:
                val = rows[0][1] if not isinstance(rows[0], dict) else rows[0].get("Value", "")
                gtid_value = str(val)
                gtid_enabled = gtid_value.upper() == "ON"
        except Exception:
            pass

        mysql_version = ""
        try:
            rows = self._query_sql("SELECT VERSION() as v") or []
            if rows:
                mysql_version = (rows[0].get("v") if isinstance(rows[0], dict) else rows[0][0]) or ""
        except Exception:
            pass

        suggested_mode = data.get("mode", "replica_mode")
        try:
            detect = self.detect_running_mode()
            dm = detect.get("msg", {}) if isinstance(detect, dict) else {}
            if dm.get("suggested_mode"):
                suggested_mode = dm["suggested_mode"]
        except Exception:
            pass

        pending_tasks = len([t for t in tasks if t.get("status") in ("pending", "running")])

        master_setup = data.get("master_setup") or None
        master_health_ok = False
        if master_setup:
            try:
                hc = self.master_health_check()
                hc_data = hc.get("msg", {}) if isinstance(hc, dict) else {}
                hc_summary = hc_data.get("summary", {})
                master_health_ok = hc_summary.get("fail", 1) == 0
            except Exception:
                pass

        server_ip = ""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            server_ip = s.getsockname()[0]
            s.close()
        except Exception:
            try:
                server_ip = socket.gethostbyname(socket.gethostname())
            except Exception:
                pass

        mysql_port = 3306
        try:
            port_rows = self._query_sql("SHOW VARIABLES LIKE 'port'")
            if port_rows:
                mysql_port = int(port_rows[0][1] if not isinstance(port_rows[0], dict) else port_rows[0].get("Value", 3306))
        except Exception:
            pass

        return self._ok(
            {
                "saved_mode": data.get("mode", "replica_mode"),
                "suggested_mode": suggested_mode,
                "mysql_version": mysql_version,
                "gtid": {"mode": gtid_value, "enabled": gtid_enabled},
                "tools": tools,
                "counts": {
                    "sources": len(sources),
                    "running_sources": running_sources,
                    "bootstrap_tasks": len(tasks),
                    "pending_tasks": pending_tasks,
                },
                "plugin_version": self.CONFIG_SCHEMA_VERSION,
                "server_ip": server_ip,
                "mysql_port": mysql_port,
                "master_setup": {
                    "configured": bool(master_setup),
                    "configured_at": master_setup.get("configured_at", "") if master_setup else "",
                    "repl_user": master_setup.get("repl_user", "") if master_setup else "",
                    "health_ok": master_health_ok,
                } if master_setup else None,
            },
            "环境扫描完成",
            "ENV_DETECTED",
        )

    def wizard_preflight_source(self, get):
        """Three-axis connectivity check for replica → master.

        Inputs: master_host, master_port, repl_user, repl_password OR
                source_id (pull from existing config).
        Returns: {network, auth, gtid} each with ok/reason.
        """
        if get is None:
            return self._fail("缺少必要参数", "ERR_PARAM_REQUIRED")
        host = getattr(get, "master_host", "") or ""
        port_str = getattr(get, "master_port", "3306") or "3306"
        user = getattr(get, "repl_user", "") or ""
        pwd = getattr(get, "repl_password", "") or ""

        if hasattr(get, "source_id") and str(get.source_id).strip():
            data = self._load_config()
            src = self._find_source(data, str(get.source_id).strip())
            if src:
                host = host or src.get("master_host", "")
                port_str = port_str or str(src.get("master_port", 3306))
                user = user or src.get("repl_user", "")
                pwd = pwd or self._decrypted_password(src)

        host = str(host).strip()
        try:
            port = int(port_str)
        except Exception:
            return self._fail("master_port 非法", "ERR_PARAM_INVALID")
        user = str(user).strip()
        pwd = str(pwd)

        if not host or not user or not pwd:
            return self._fail("缺少主库地址/账号/密码", "ERR_PARAM_REQUIRED")

        result = {
            "network": {"ok": False, "reason": ""},
            "auth": {"ok": False, "reason": ""},
            "gtid": {"ok": False, "reason": ""},
        }

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        try:
            sock.connect((host, port))
            result["network"] = {"ok": True, "reason": "TCP 已连通"}
        except Exception as ex:
            result["network"] = {"ok": False, "reason": self._classify_connectivity_error(ex) + ": " + str(ex)}
        finally:
            try:
                sock.close()
            except Exception:
                pass

        if result["network"]["ok"] and self._check_command_exists("mysql"):
            r = self._run_shell(
                ["mysql", "-h", host, "-P", str(port), "-u", user, "-e",
                 "SELECT 1; SHOW VARIABLES LIKE 'gtid_mode'"],
                env_extra={"MYSQL_PWD": pwd},
                timeout=15,
            )
            if r["code"] == 0:
                result["auth"] = {"ok": True, "reason": "账号可执行查询"}
                stdout = r.get("stdout") or ""
                if "ON" in stdout:
                    result["gtid"] = {"ok": True, "reason": "gtid_mode=ON"}
                else:
                    result["gtid"] = {"ok": False, "reason": "主库 gtid_mode 非 ON，建议开启"}
            else:
                err = (r.get("stderr") or "").strip()
                reason = self._classify_connectivity_error(err)
                result["auth"] = {"ok": False, "reason": reason + ": " + err[:200]}
                result["gtid"] = {"ok": False, "reason": "账号校验未通过，无法检测 GTID"}
        else:
            if not self._check_command_exists("mysql"):
                result["auth"] = {"ok": False, "reason": "本机未安装 mysql 客户端，无法校验账号"}
                result["gtid"] = {"ok": False, "reason": "无法检测"}

        all_ok = all(result[k]["ok"] for k in ("network", "auth", "gtid"))
        return self._ok({"checks": result, "all_ok": all_ok}, "连通性检查完成", "PREFLIGHT_OK")

    def wizard_list_master_dbs(self, get):
        """SHOW DATABASES on master (filtered to user DBs) with size estimate."""
        if get is None:
            return self._fail("缺少必要参数", "ERR_PARAM_REQUIRED")

        host = getattr(get, "master_host", "") or ""
        port_str = getattr(get, "master_port", "3306") or "3306"
        user = getattr(get, "repl_user", "") or ""
        pwd = getattr(get, "repl_password", "") or ""

        if hasattr(get, "source_id") and str(get.source_id).strip():
            data = self._load_config()
            src = self._find_source(data, str(get.source_id).strip())
            if src:
                host = host or src.get("master_host", "")
                port_str = port_str or str(src.get("master_port", 3306))
                user = user or src.get("repl_user", "")
                pwd = pwd or self._decrypted_password(src)

        host = str(host).strip()
        try:
            port = int(port_str)
        except Exception:
            return self._fail("master_port 非法", "ERR_PARAM_INVALID")
        user = str(user).strip()

        if not host or not user or not pwd:
            return self._fail("缺少主库连接信息", "ERR_PARAM_REQUIRED")
        if not self._check_command_exists("mysql"):
            return self._fail("本机缺少 mysql 客户端，无法远程读库列表", "ERR_NO_MYSQL_CLIENT")

        sys_dbs = {"mysql", "sys", "information_schema", "performance_schema"}

        sql = (
            "SELECT s.SCHEMA_NAME AS db, "
            "COALESCE(SUM(t.DATA_LENGTH+t.INDEX_LENGTH)/1024/1024,0) AS size_mb "
            "FROM information_schema.SCHEMATA s "
            "LEFT JOIN information_schema.TABLES t ON t.TABLE_SCHEMA=s.SCHEMA_NAME "
            "GROUP BY s.SCHEMA_NAME ORDER BY size_mb DESC"
        )
        r = self._run_shell(
            ["mysql", "-h", host, "-P", str(port), "-u", user, "-N", "-B", "-e", sql],
            env_extra={"MYSQL_PWD": pwd},
            timeout=30,
        )

        dbs = []
        if r["code"] == 0:
            for line in (r.get("stdout") or "").splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t")
                if not parts:
                    continue
                name = parts[0].strip()
                if not name or name in sys_dbs:
                    continue
                try:
                    size_mb = float(parts[1]) if len(parts) > 1 else 0.0
                except Exception:
                    size_mb = 0.0
                dbs.append({"name": name, "size_mb": round(size_mb, 2)})
        else:
            r2 = self._run_shell(
                ["mysql", "-h", host, "-P", str(port), "-u", user, "-N", "-B", "-e", "SHOW DATABASES"],
                env_extra={"MYSQL_PWD": pwd},
                timeout=15,
            )
            if r2["code"] != 0:
                return self._fail(
                    "读取主库库列表失败: " + (r2.get("stderr") or r.get("stderr") or "").strip()[:200],
                    "ERR_LIST_DBS",
                )
            for line in (r2.get("stdout") or "").splitlines():
                name = line.strip()
                if not name or name in sys_dbs:
                    continue
                dbs.append({"name": name, "size_mb": 0.0})

        return self._ok({"databases": dbs, "count": len(dbs)}, "主库库列表读取成功", "LIST_DBS_OK")

    def wizard_recommend_bootstrap(self, get=None):
        """Recommend physical vs logical based on tools + upstream size."""
        size_mb = 0.0
        if get is not None and hasattr(get, "size_mb"):
            try:
                size_mb = float(get.size_mb)
            except Exception:
                size_mb = 0.0

        tools_resp = self.check_bootstrap_tools()
        tools = tools_resp.get("msg", {}) if isinstance(tools_resp, dict) else {}
        physical_ready = bool(tools.get("physical_ready"))
        logical_ready = bool(tools.get("logical_ready"))

        mode = "logical"
        reason = "默认使用 mysqldump 逻辑通道，兼容性最好"
        if size_mb >= 20480 and physical_ready:
            mode = "physical"
            reason = "数据量 ≥ 20GB 且已安装物理备份工具，建议 physical 加速"
        elif size_mb >= 2048 and physical_ready:
            mode = "physical"
            reason = "数据量 ≥ 2GB 且已安装物理备份工具，建议 physical"
        elif physical_ready and size_mb >= 512:
            mode = "auto"
            reason = "可尝试 auto，由引擎自动选择 physical/logical"
        if not logical_ready and not physical_ready:
            mode = "unavailable"
            reason = "未检测到 mysqldump 与物理工具，请先在高级操作中安装"

        return self._ok(
            {
                "recommended_mode": mode,
                "reason": reason,
                "size_mb": size_mb,
                "tools": tools,
            },
            "初始化策略已生成",
            "RECOMMEND_OK",
        )

    def wizard_start_replication(self, get):
        """Atomic one-shot: add_source + set_db_mappings + create + trigger.

        Accepts:
          source_id?, channel_name?, master_host, master_port, repl_user,
          repl_password, mappings (list of {source_db, target_db} or list of
          source_db strings; if strings, target_db = `<source_id>_<db>`),
          mode (auto/physical/logical), auto_start (bool)
        Returns: {source_id, task_id, worker_id, channel_name, mode}
        """
        if get is None:
            return self._fail("缺少必要参数", "ERR_PARAM_REQUIRED")

        required = ["master_host", "master_port", "repl_user", "repl_password"]
        for k in required:
            if not hasattr(get, k) or not str(getattr(get, k)).strip():
                return self._fail("缺少参数: {}".format(k), "ERR_PARAM_REQUIRED")

        # auto-generate source_id/channel if not supplied
        data = self._load_config()
        existing = [s.get("source_id") for s in data.get("sources", [])]
        source_id = (str(getattr(get, "source_id", "")) or "").strip()
        if not source_id:
            idx = 1
            while True:
                candidate = "m{}".format(idx)
                if candidate not in existing:
                    source_id = candidate
                    break
                idx += 1
        channel_name = (str(getattr(get, "channel_name", "")) or "").strip()
        if not channel_name:
            channel_name = "ch_" + re.sub(r"[^A-Za-z0-9_]", "_", source_id)

        if not self._validate_source_id(source_id):
            return self._fail("source_id 非法", "ERR_PARAM_INVALID")
        if not self._validate_channel_name(channel_name):
            return self._fail("channel_name 非法", "ERR_PARAM_INVALID")
        for s in data.get("sources", []):
            if s.get("source_id") != source_id and s.get("channel_name") == channel_name:
                return self._fail("该通道名称已被其他来源占用", "ERR_DUPLICATE")

        # parse mappings (either list of dicts or list of strings)
        raw_mappings = getattr(get, "mappings", "") or ""
        if isinstance(raw_mappings, str):
            try:
                raw_mappings = json.loads(raw_mappings) if raw_mappings.strip() else []
            except Exception:
                return self._fail("mappings 格式非法（需 JSON）", "ERR_PARAM_INVALID")
        if not isinstance(raw_mappings, list) or not raw_mappings:
            return self._fail("请至少选择一个要同步的库", "ERR_MAPPINGS_EMPTY")

        normalized = []
        for m in raw_mappings:
            if isinstance(m, str):
                src = m.strip()
                if not src:
                    continue
                tgt = "{}_{}".format(source_id, src)
            elif isinstance(m, dict):
                src = str(m.get("source_db", "")).strip()
                tgt = str(m.get("target_db", "")).strip() or "{}_{}".format(source_id, src)
            else:
                return self._fail("mapping 项格式非法", "ERR_PARAM_INVALID")
            if not src or not tgt:
                return self._fail("source_db/target_db 不可为空", "ERR_PARAM_INVALID")
            if not self._validate_mysql_scope_name(src) or not self._validate_mysql_scope_name(tgt):
                return self._fail("库名只能含字母/数字/下划线/-/$", "ERR_PARAM_INVALID")
            normalized.append({"source_db": src, "target_db": tgt})

        # create source (idempotent: if exists then update in-place)
        data = self._load_config()
        existing_source = self._find_source(data, source_id)
        source_existed = bool(existing_source)
        conflicts = self._collect_target_db_conflicts(normalized, exclude_source_id=source_id if source_existed else "")
        if conflicts:
            return self._fail(
                "目标库名存在冲突，请先调整后再继续",
                "ERR_TARGET_DB_CONFLICT",
                {"conflicts": conflicts},
            )

        previous_source_snapshot = copy.deepcopy(existing_source) if existing_source else None
        if existing_source:
            existing_source["master_host"] = str(getattr(get, "master_host")).strip()
            existing_source["master_port"] = int(getattr(get, "master_port"))
            existing_source["repl_user"] = str(getattr(get, "repl_user")).strip()
            existing_source["repl_password"] = self._crypto_encrypt(str(getattr(get, "repl_password")))
            existing_source["channel_name"] = channel_name
            existing_source["updated_at"] = self._now()
            self._append_log(source_id, "检测到重复接入，已更新连接信息并继续流程")
            self._save_config(data)
        else:
            add_payload = public.to_dict_obj({
                "source_id": source_id,
                "channel_name": channel_name,
                "master_host": getattr(get, "master_host"),
                "master_port": getattr(get, "master_port"),
                "repl_user": getattr(get, "repl_user"),
                "repl_password": getattr(get, "repl_password"),
            })
            add_resp = self.add_source(add_payload)
            if not add_resp.get("status"):
                return add_resp

        # write mappings
        map_resp = self.set_db_mappings(public.to_dict_obj({
            "source_id": source_id,
            "mappings": json.dumps(normalized),
        }))
        if not map_resp.get("status"):
            # Existing sources must never be deleted as a rollback side effect.
            if source_existed:
                latest = self._load_config()
                live_source = self._find_source(latest, source_id)
                if live_source and previous_source_snapshot:
                    live_source.clear()
                    live_source.update(previous_source_snapshot)
                    live_source["updated_at"] = self._now()
                    self._save_config(latest)
                    self._append_log(source_id, "重复接入流程失败，已恢复原连接配置")
            else:
                self.remove_source(public.to_dict_obj({"source_id": source_id}))
            return map_resp

        mode = (str(getattr(get, "mode", "auto")) or "auto").strip().lower()
        if mode not in ("auto", "physical", "logical"):
            mode = "auto"

        task_id = None
        if source_existed:
            latest_live_task = None
            current = self._load_config()
            for t in current.get("bootstrap_tasks", []):
                if t.get("source_id") == source_id and t.get("status") in ("pending", "running"):
                    latest_live_task = t
                    break
            if latest_live_task:
                task_id = latest_live_task.get("task_id")

        if not task_id:
            task_resp = self.create_bootstrap_task(public.to_dict_obj({
                "source_id": source_id,
                "mode": mode,
            }))
            if not task_resp.get("status"):
                return task_resp
            task = task_resp.get("msg", {}) if isinstance(task_resp.get("msg"), dict) else {}
            task_id = task.get("task_id") if isinstance(task, dict) else None

        auto_start = str(getattr(get, "auto_start", "1")).strip().lower() in ("1", "true", "yes", "on")
        worker_id = ""
        if auto_start and task_id:
            trig = self.trigger_bootstrap_task(public.to_dict_obj({"task_id": task_id}))
            if trig.get("status"):
                trig_msg = trig.get("msg", {})
                if isinstance(trig_msg, dict):
                    worker_id = trig_msg.get("worker_id", "")

        return self._ok(
            {
                "source_id": source_id,
                "channel_name": channel_name,
                "task_id": task_id,
                "worker_id": worker_id,
                "mode": mode,
                "mappings_count": len(normalized),
                "auto_start": auto_start,
                "source_existed": source_existed,
            },
            "检测到重复接入，已更新配置并继续执行任务" if source_existed else ("已完成接入，任务已开始后台执行" if auto_start else "已完成接入，等待手动触发"),
            "WIZARD_START_OK",
        )

    def register_existing_target_dbs(self, get=None):
        """Register every known target DB into BaoTa's panel list.

        Useful for sources that finished before the auto-register logic was
        added. UI calls this from the dashboard's "同步到宝塔数据库列表" button.
        """
        data = self._load_config()
        sources = data.get("sources", []) or []
        registered = []
        skipped = []
        for src in sources:
            for m in src.get("db_mappings", []) or []:
                tgt = str(m.get("target_db", "")).strip()
                if not tgt:
                    continue
                try:
                    note = self._register_db_in_panel(tgt, source_id=src.get("source_id"))
                    if note:
                        registered.append({"db": tgt, "note": note})
                    else:
                        skipped.append(tgt)
                except Exception as ex:
                    skipped.append("{}({})".format(tgt, ex))
        return public.returnMsg(True, {
            "registered": registered,
            "skipped_or_existing": skipped,
        })

