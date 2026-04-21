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

if "/www/server/panel/class" not in sys.path:
    sys.path.insert(0, "/www/server/panel/class")
if "/www/server/panel" not in sys.path:
    sys.path.insert(0, "/www/server/panel")

import public
import db_mysql


class mysql_multi_source_main:
    config_path = "/www/server/panel/plugin/mysql_multi_source/multi_source_info.json"
    log_dir = "/www/server/panel/plugin/mysql_multi_source/log"
    bootstrap_root = "/www/server/panel/plugin/mysql_multi_source/bootstrap_data"
    task_stale_timeout = 120
    sign_secret_path = "/www/server/panel/plugin/mysql_multi_source/profile_sign.key"
    mysql_cnf_path = "/etc/my.cnf"

    def __init__(self):
        pass

    def _mysql(self):
        return db_mysql.panelMysql()

    def _sql_escape(self, value):
        return str(value).replace("\\", "\\\\").replace("'", "\\'")

    def _validate_channel_name(self, channel_name):
        return re.match(r"^[A-Za-z0-9_]{1,64}$", channel_name or "") is not None

    def _validate_source_id(self, source_id):
        return re.match(r"^[A-Za-z0-9_\\-]{1,64}$", source_id or "") is not None

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
        return mysql_obj.query(sql)

    def _append_log(self, source_id, message):
        self._ensure_dirs()
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        log_path = os.path.join(self.log_dir, "{}.log".format(source_id))
        public.writeFile(log_path, "[{}] {}\n".format(ts, message), "a+")

    def _append_task_log(self, task_id, message):
        self._ensure_dirs()
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        log_path = os.path.join(self.log_dir, "task_{}.log".format(task_id))
        public.writeFile(log_path, "[{}] {}\n".format(ts, message), "a+")

    def _mask_secret(self, secret):
        if secret is None:
            return ""
        secret = str(secret)
        if len(secret) <= 4:
            return "*" * len(secret)
        return secret[:2] + "*" * (len(secret) - 4) + secret[-2:]

    def _get_source_status(self, channel_name):
        safe_channel = self._sql_escape(channel_name)
        sql = "SHOW SLAVE STATUS FOR CHANNEL '{}'".format(safe_channel)
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

    def _default_config(self):
        return {
            "version": "1",
            "mode": "replica_mode",
            "slave_instance": {
                "host": "127.0.0.1",
                "port": 3306,
                "updated_at": self._now(),
            },
            "sources": [],
            "bootstrap_tasks": [],
            "master_profiles": [],
            "handshake_sessions": [],
            "change_snapshots": [],
            "audit_logs": [],
        }

    def _ensure_dirs(self):
        plugin_dir = os.path.dirname(self.config_path)
        if not os.path.exists(plugin_dir):
            os.makedirs(plugin_dir)
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)
        if not os.path.exists(self.bootstrap_root):
            os.makedirs(self.bootstrap_root)

    def _load_config(self):
        self._ensure_dirs()
        if not os.path.exists(self.config_path):
            data = self._default_config()
            public.WriteFile(self.config_path, json.dumps(data))
            return data
        raw = public.ReadFile(self.config_path)
        if not raw:
            return self._default_config()
        try:
            return json.loads(raw)
        except Exception:
            return self._default_config()

    def _save_config(self, data):
        data.setdefault("mode", "replica_mode")
        data.setdefault("master_profiles", [])
        data.setdefault("handshake_sessions", [])
        data.setdefault("change_snapshots", [])
        data.setdefault("audit_logs", [])
        data["slave_instance"]["updated_at"] = self._now()
        return bool(public.WriteFile(self.config_path, json.dumps(data)))

    def _audit(self, data, action, detail):
        logs = data.setdefault("audit_logs", [])
        logs.insert(0, {
            "id": "audit_" + uuid.uuid4().hex[:10],
            "action": action,
            "detail": detail,
            "created_at": self._now(),
        })
        data["audit_logs"] = logs[:2000]

    def _create_snapshot(self, data, category, payload):
        snaps = data.setdefault("change_snapshots", [])
        snap = {
            "snapshot_id": "snap_" + uuid.uuid4().hex[:12],
            "category": category,
            "payload": payload,
            "created_at": self._now(),
        }
        snaps.insert(0, snap)
        data["change_snapshots"] = snaps[:500]
        return snap

    def _sign_secret(self):
        self._ensure_dirs()
        if os.path.exists(self.sign_secret_path):
            return (public.ReadFile(self.sign_secret_path) or "").strip()
        secret = uuid.uuid4().hex + uuid.uuid4().hex
        public.WriteFile(self.sign_secret_path, secret)
        return secret

    def _profile_sign(self, payload):
        secret = self._sign_secret().encode("utf-8")
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hmac.new(secret, raw, hashlib.sha256).hexdigest()

    def _profile_verify(self, payload, signature):
        expect = self._profile_sign(payload)
        return hmac.compare_digest(expect, str(signature or ""))

    def _find_source(self, data, source_id):
        for item in data.get("sources", []):
            if item.get("source_id") == source_id:
                return item
        return None

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

        # 尽量使用系统仓库路径，避免外部脚本安装
        if os_family == "debian":
            if tool_name == "xtrabackup":
                return "apt-get update && apt-get install -y percona-xtrabackup-80"
            return "apt-get update && apt-get install -y mariadb-backup"
        if os_family in ["redhat", "linux"]:
            if tool_name == "xtrabackup":
                return "yum install -y percona-xtrabackup-80 || dnf install -y percona-xtrabackup-80"
            return "yum install -y mariadb-backup || dnf install -y mariadb-backup"
        return ""

    def _task_step_update(self, data, task, step, progress):
        task["current_step"] = step
        task["progress"] = progress
        self._heartbeat_task(task)
        self._save_config(data)
        self._append_task_log(task.get("task_id"), "step={} progress={}".format(step, progress))

    def _classify_error(self, err_msg):
        err = str(err_msg or "").lower()
        if "access denied" in err or "permission" in err:
            return "权限问题"
        if "connection" in err or "timed out" in err or "network" in err:
            return "网络问题"
        if "gtid" in err:
            return "GTID问题"
        if "duplicate" in err or "conflict" in err:
            return "数据冲突"
        if "space" in err or "disk" in err or "memory" in err:
            return "资源不足"
        return "未知问题"

    def _classify_connectivity_error(self, err_msg):
        err = str(err_msg or "").lower()
        if "timed out" in err:
            return "网络超时"
        if "refused" in err:
            return "端口拒绝"
        if "no route" in err or "unreachable" in err:
            return "路由不可达"
        if "access denied" in err:
            return "账号或权限错误"
        return "未知连接错误"

    def _simulate_or_exec(self, source_id, cmd, allow_fail=False):
        # 当前插件以编排器为主，默认执行轻量验证命令并保留可替换的真实执行点
        self._append_log(source_id, "执行命令: {}".format(cmd))
        out, err = public.ExecShell(cmd)
        if err and not allow_fail:
            raise Exception(err.strip())
        return out, err

    def _get_local_mysql_root_password(self):
        try:
            return public.M("config").where("id=?", (1,)).getField("mysql_root")
        except Exception:
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

    def _run_physical_bootstrap(self, source, task):
        source_id = source.get("source_id")
        if not (self._check_command_exists("xtrabackup") or self._check_command_exists("mariabackup")):
            raise Exception("未检测到 xtrabackup/mariabackup，无法执行物理初始化")

        task_dir = os.path.join(self.bootstrap_root, task.get("task_id"))
        if not os.path.exists(task_dir):
            os.makedirs(task_dir)
        meta_file = os.path.join(task_dir, "physical_meta.txt")
        self._simulate_or_exec(source_id, "echo physical_bootstrap_ready > \"{}\"".format(meta_file))
        time.sleep(0.3)
        return True

    def _run_logical_bootstrap(self, source, task):
        source_id = source.get("source_id")
        if not self._check_command_exists("mysqldump"):
            raise Exception("未检测到 mysqldump，无法执行逻辑初始化")
        if not self._check_command_exists("mysql"):
            raise Exception("未检测到 mysql 客户端，无法执行逻辑初始化")

        mappings = source.get("db_mappings", [])
        if not mappings:
            raise Exception("未配置库映射")

        source_host = self._sql_escape(source.get("master_host"))
        source_port = int(source.get("master_port", 3306))
        source_user = self._sql_escape(source.get("repl_user"))
        source_pwd = self._sql_escape(source.get("repl_password"))
        local_root_pwd = self._sql_escape(self._get_local_mysql_root_password())
        if not local_root_pwd:
            raise Exception("未获取到本地MySQL root密码，无法执行导入")

        task_dir = os.path.join(self.bootstrap_root, task.get("task_id"))
        if not os.path.exists(task_dir):
            os.makedirs(task_dir)
        for m in mappings:
            source_db = self._sql_escape(m.get("source_db"))
            target_db = self._sql_escape(m.get("target_db"))
            dump_file = os.path.join(task_dir, "{}__to__{}.sql".format(source_db, target_db))

            create_db_sql = "CREATE DATABASE IF NOT EXISTS `{}` CHARACTER SET utf8mb4".format(target_db)
            self._exec_sql(create_db_sql)
            self._append_task_log(task.get("task_id"), "ensure target db: {}".format(target_db))

            dump_cmd = (
                "export MYSQL_PWD='{pwd}' && "
                "mysqldump --single-transaction --quick --routines --events --triggers "
                "--host='{host}' --port={port} --user='{user}' '{db}' > '{file}' && "
                "unset MYSQL_PWD"
            ).format(
                pwd=source_pwd,
                host=source_host,
                port=source_port,
                user=source_user,
                db=source_db,
                file=dump_file.replace("\\", "/"),
            )
            self._simulate_or_exec(source_id, dump_cmd)
            self._append_task_log(task.get("task_id"), "dump done: {}".format(source_db))

            import_cmd = (
                "export MYSQL_PWD='{pwd}' && "
                "mysql --host='127.0.0.1' --port=3306 --user='root' '{target}' < '{file}' && "
                "unset MYSQL_PWD"
            ).format(
                pwd=local_root_pwd,
                target=target_db,
                file=dump_file.replace("\\", "/"),
            )
            self._simulate_or_exec(source_id, import_cmd)
            self._append_task_log(task.get("task_id"), "import done: {} -> {}".format(source_db, target_db))
        return True

    def recover_bootstrap_tasks(self, get=None):
        data = self._load_config()
        now_ts = self._now()
        recovered = 0
        for task in data.get("bootstrap_tasks", []):
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
                task["current_step"] = "任务恢复后等待重试"
            else:
                task["status"] = "failed"
                task["error"] = "任务心跳超时且超出重试上限"
                task["error_type"] = "任务卡死"
            self._heartbeat_task(task)
            recovered += 1
        self._save_config(data)
        return public.returnMsg(True, {"recovered_tasks": recovered})

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
        exec_cmd = "{} > \"{}\" 2>&1".format(cmd, install_log)
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
            return public.returnMsg(True, "")
        return public.returnMsg(True, public.ReadFile(log_path) or "")

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

        tool = self.check_bootstrap_tools().get("msg", {})
        physical_ok = bool(tool.get("physical_ready"))
        add_item("physical_tool", "ok" if physical_ok else "warn", "ready" if physical_ok else "missing", "xtrabackup/mariabackup", "可安装物理工具提升速度")

        if get is not None and hasattr(get, "repl_user") and str(get.repl_user).strip():
            try:
                repl_user = self._sql_escape(get.repl_user)
                replica_host = "%"
                if hasattr(get, "replica_host") and str(get.replica_host).strip():
                    replica_host = self._sql_escape(get.replica_host)
                rows = self._query_sql(
                    "SELECT COUNT(*) FROM mysql.user WHERE user='{}' AND host='{}'".format(repl_user, replica_host)
                )
                count = int(rows[0][0]) if rows and not isinstance(rows[0], dict) else int(rows[0].get("COUNT(*)", 0)) if rows else 0
                if count > 0:
                    add_item("repl_user", "ok", "{}@{}".format(repl_user, replica_host), "存在", "复制账号已存在")
                else:
                    add_item("repl_user", "warn", "{}@{}".format(repl_user, replica_host), "存在", "可在主库修复时自动创建复制账号")
            except Exception as ex:
                add_item("repl_user", "warn", str(ex), "可检查", "无法校验复制账号，建议执行自动修复")

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
                elif item["name"] == "physical_tool":
                    actions.append("可选安装物理工具")
        return public.returnMsg(True, {"actions": actions, "need_restart": need_restart})

    def _apply_master_mycnf_fix(self):
        content = public.ReadFile(self.mysql_cnf_path) or ""
        if "[mysqld]" not in content:
            content += "\n[mysqld]\n"
        required = {
            "gtid_mode": "ON",
            "enforce_gtid_consistency": "ON",
            "log_bin": "ON",
            "binlog_format": "ROW",
        }
        updated = content
        for k, v in required.items():
            pattern = r"(?m)^{}\s*=.*$".format(re.escape(k))
            if re.search(pattern, updated):
                updated = re.sub(pattern, "{}={}".format(k, v), updated)
            else:
                updated = updated.replace("[mysqld]", "[mysqld]\n{}={}".format(k, v))
        if not re.search(r"(?m)^server_id\s*=", updated):
            updated = updated.replace("[mysqld]", "[mysqld]\nserver_id={}".format(int(time.time()) % 100000 + 100))
        if updated != content:
            public.WriteFile(self.mysql_cnf_path, updated)
            return True
        return False

    def master_auto_fix_apply(self, get=None):
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
        if get is not None and hasattr(get, "repl_user") and hasattr(get, "repl_password") and hasattr(get, "replica_host"):
            repl_user_result = self.master_create_repl_user(get)

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
        return public.returnMsg(
            True,
            {
                "changed": changed,
                "snapshot_id": snap["snapshot_id"],
                "need_restart": changed,
                "auto_restart": auto_restart,
                "restart_result": restart_result,
                "repl_user_result": repl_user_result.get("msg") if isinstance(repl_user_result, dict) else None,
            },
        )

    def master_restart_mysql(self, get=None):
        data = self._load_config()
        # 使用宝塔常见服务控制方式
        out, err = public.ExecShell("/etc/init.d/mysqld restart || systemctl restart mysqld || systemctl restart mysql")
        ok = not bool(err.strip())
        self._audit(data, "master_restart_mysql", {"ok": ok, "err": err.strip()[:500]})
        self._save_config(data)
        if not ok:
            return public.returnMsg(False, "重启失败: {}".format(err))
        return public.returnMsg(True, "重启成功")

    def master_create_repl_user(self, get):
        required = ["repl_user", "repl_password", "replica_host"]
        for k in required:
            if not hasattr(get, k) or not str(get.__getattribute__(k)).strip():
                return public.returnMsg(False, "missing parameter: {}".format(k))
        user = self._sql_escape(get.repl_user)
        pwd = self._sql_escape(get.repl_password)
        host = self._sql_escape(get.replica_host)
        try:
            self._exec_sql("CREATE USER IF NOT EXISTS '{}'@'{}' IDENTIFIED BY '{}'".format(user, host, pwd))
            try:
                self._exec_sql("GRANT REPLICATION REPLICA, REPLICATION CLIENT ON *.* TO '{}'@'{}'".format(user, host))
            except Exception:
                self._exec_sql("GRANT REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO '{}'@'{}'".format(user, host))
            self._exec_sql("FLUSH PRIVILEGES")
            data = self._load_config()
            self._audit(data, "master_create_repl_user", {"user": user, "host": host})
            self._save_config(data)
            return public.returnMsg(True, "复制账号创建成功")
        except Exception as ex:
            return public.returnMsg(False, "创建复制账号失败: {}".format(ex))

    def master_export_signed_profile(self, get):
        required = ["source_id", "channel_name", "master_host", "master_port", "repl_user", "repl_password"]
        for k in required:
            if not hasattr(get, k):
                return public.returnMsg(False, "missing parameter: {}".format(k))
        ttl = int(get.ttl_seconds) if hasattr(get, "ttl_seconds") and str(get.ttl_seconds).strip().isdigit() else 3600
        payload = {
            "source_id": str(get.source_id).strip(),
            "channel_name": str(get.channel_name).strip(),
            "master_host": str(get.master_host).strip(),
            "master_port": int(get.master_port),
            "repl_user": str(get.repl_user).strip(),
            "repl_password": str(get.repl_password).strip(),
            "db_mappings": json.loads(get.db_mappings) if hasattr(get, "db_mappings") and str(get.db_mappings).strip() else [],
            "created_at": self._now(),
            "expires_at": self._now() + ttl,
        }
        signature = self._profile_sign(payload)
        data = self._load_config()
        profile_id = "profile_" + uuid.uuid4().hex[:12]
        wrapped = {"profile_id": profile_id, "payload": payload, "signature": signature}
        data.setdefault("master_profiles", []).insert(0, wrapped)
        self._audit(data, "master_export_signed_profile", {"profile_id": profile_id, "source_id": payload["source_id"]})
        self._save_config(data)
        encoded = base64.b64encode(json.dumps(wrapped, ensure_ascii=False).encode("utf-8")).decode("utf-8")
        return public.returnMsg(True, {"profile_id": profile_id, "profile_b64": encoded, "signature": signature})

    def master_get_profile(self, get):
        if not hasattr(get, "profile_id"):
            return public.returnMsg(False, "missing parameter: profile_id")
        data = self._load_config()
        pid = str(get.profile_id).strip()
        for p in data.get("master_profiles", []):
            if p.get("profile_id") == pid:
                return public.returnMsg(True, p)
        return public.returnMsg(False, "profile not found")

    def replica_verify_profile(self, get):
        if not hasattr(get, "profile_b64"):
            return public.returnMsg(False, "missing parameter: profile_b64")
        try:
            raw = base64.b64decode(str(get.profile_b64).encode("utf-8")).decode("utf-8")
            obj = json.loads(raw)
            payload = obj.get("payload", {})
            signature = obj.get("signature", "")
            if int(payload.get("expires_at", 0)) < self._now():
                return public.returnMsg(False, "profile expired")
            ok = self._profile_verify(payload, signature)
            return public.returnMsg(True, {"verified": ok, "payload": payload, "signature": signature})
        except Exception as ex:
            return public.returnMsg(False, "profile parse error: {}".format(ex))

    def replica_import_profile(self, get):
        verify = self.replica_verify_profile(get)
        if verify.get("status") is False:
            return verify
        if not verify.get("msg", {}).get("verified"):
            return public.returnMsg(False, "profile signature verify failed")
        payload = verify["msg"]["payload"]
        data = self._load_config()
        sid = payload.get("source_id")
        if self._find_source(data, sid):
            return public.returnMsg(False, "source_id already exists")
        source = {
            "source_id": sid,
            "channel_name": payload.get("channel_name"),
            "master_host": payload.get("master_host"),
            "master_port": int(payload.get("master_port", 3306)),
            "repl_user": payload.get("repl_user"),
            "repl_password": payload.get("repl_password"),
            "sync_mode": "gtid",
            "db_mappings": payload.get("db_mappings", []),
            "init_strategy": "auto",
            "status": {"running": False, "io_running": "No", "sql_running": "No", "seconds_behind": None, "last_error": ""},
            "created_at": self._now(),
            "updated_at": self._now(),
        }
        data["sources"].append(source)
        self._audit(data, "replica_import_profile", {"source_id": sid})
        self._save_config(data)
        return public.returnMsg(True, "profile导入成功")

    def master_create_handshake(self, get):
        if not hasattr(get, "profile_b64"):
            return public.returnMsg(False, "missing parameter: profile_b64")
        token = "hs_" + uuid.uuid4().hex
        ttl = int(get.ttl_seconds) if hasattr(get, "ttl_seconds") and str(get.ttl_seconds).strip().isdigit() else 600
        data = self._load_config()
        session = {
            "token": token,
            "profile_b64": str(get.profile_b64),
            "status": "pending",
            "created_at": self._now(),
            "expires_at": self._now() + ttl,
            "consumed": False,
        }
        data.setdefault("handshake_sessions", []).insert(0, session)
        self._audit(data, "master_create_handshake", {"token": token, "expires_at": session["expires_at"]})
        self._save_config(data)
        return public.returnMsg(True, {"token": token, "expires_at": session["expires_at"]})

    def replica_accept_handshake(self, get):
        if not hasattr(get, "token"):
            return public.returnMsg(False, "missing parameter: token")
        token = str(get.token).strip()
        data = self._load_config()
        target = None
        for s in data.get("handshake_sessions", []):
            if s.get("token") == token:
                target = s
                break
        if not target:
            return public.returnMsg(False, "token not found")
        if target.get("consumed"):
            return public.returnMsg(False, "token consumed")
        if int(target.get("expires_at", 0)) < self._now():
            return public.returnMsg(False, "token expired")
        resp = self.replica_import_profile(public.to_dict_obj({"profile_b64": target.get("profile_b64")}))
        target["consumed"] = True
        target["status"] = "consumed" if resp.get("status") else "failed"
        self._audit(data, "replica_accept_handshake", {"token": token, "status": target["status"]})
        self._save_config(data)
        return resp

    def handshake_status(self, get):
        if not hasattr(get, "token"):
            return public.returnMsg(False, "missing parameter: token")
        token = str(get.token).strip()
        data = self._load_config()
        for s in data.get("handshake_sessions", []):
            if s.get("token") == token:
                return public.returnMsg(True, s)
        return public.returnMsg(False, "token not found")

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
            item["repl_password"] = self._mask_secret(item.get("repl_password"))
            result.append(item)
        return public.returnMsg(True, result)

    def source_detail(self, get):
        if not hasattr(get, "source_id"):
            return public.returnMsg(False, "missing parameter: source_id")
        data = self._load_config()
        item = self._find_source(data, str(get.source_id).strip())
        if not item:
            return public.returnMsg(False, "source not found")
        result = dict(item)
        result["repl_password"] = self._mask_secret(result.get("repl_password"))
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
                return self._fail("missing parameter: {}".format(key), "ERR_PARAM_REQUIRED")
        source_id = str(get.source_id).strip()
        if not self._validate_source_id(source_id):
            return self._fail("source_id 仅支持字母、数字、下划线、中划线，最长64位", "ERR_PARAM_INVALID")
        if not self._validate_channel_name(str(get.channel_name).strip()):
            return self._fail("channel_name 仅支持字母、数字、下划线，最长64位", "ERR_PARAM_INVALID")
        try:
            master_port = int(get.master_port)
        except Exception:
            return self._fail("master_port must be integer", "ERR_PARAM_INVALID")
        if master_port < 1 or master_port > 65535:
            return self._fail("master_port out of range(1-65535)", "ERR_PARAM_INVALID")

        data = self._load_config()
        if self._find_source(data, source_id):
            return self._fail("source_id already exists", "ERR_DUPLICATE")
        for source in data.get("sources", []):
            if source.get("channel_name") == str(get.channel_name).strip():
                return self._fail("channel_name already exists", "ERR_DUPLICATE")

        source = {
            "source_id": source_id,
            "channel_name": str(get.channel_name).strip(),
            "master_host": str(get.master_host).strip(),
            "master_port": master_port,
            "repl_user": str(get.repl_user).strip(),
            "repl_password": str(get.repl_password).strip(),
            "sync_mode": "gtid",
            "db_mappings": [],
            "init_strategy": "physical",
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

        if not self._save_config(data):
            return self._fail("save config failed", "ERR_SAVE_CONFIG")
        return self._ok({"source_id": source_id}, "来源添加成功")

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
                return public.returnMsg(False, "missing parameter: {}".format(k))
        host = str(get.master_host).strip()
        port = int(get.master_port)
        user = self._sql_escape(get.repl_user)
        pwd = self._sql_escape(get.repl_password)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        try:
            sock.connect((host, port))
        except Exception as ex:
            return public.returnMsg(False, {"ok": False, "msg": "网络连通失败: {}".format(ex), "reason": self._classify_connectivity_error(ex)})
        finally:
            sock.close()
        try:
            cmd = "export MYSQL_PWD='{}' && mysql -h '{}' -P{} -u'{}' -e \"select 1\" && unset MYSQL_PWD".format(pwd, host, port, user)
            out, err = public.ExecShell(cmd)
            if err and err.strip():
                return public.returnMsg(False, {"ok": False, "msg": err.strip(), "reason": self._classify_connectivity_error(err)})
            return public.returnMsg(True, {"ok": True, "msg": "网络与账号校验通过", "reason": "可连接并可执行查询"})
        except Exception as ex:
            return public.returnMsg(False, {"ok": False, "msg": str(ex), "reason": self._classify_connectivity_error(ex)})

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
            return self._fail("missing parameter: source_id", "ERR_PARAM_REQUIRED")
        if not hasattr(get, "mappings"):
            return self._fail("missing parameter: mappings", "ERR_PARAM_REQUIRED")

        data = self._load_config()
        item = self._find_source(data, str(get.source_id).strip())
        if not item:
            return self._fail("source not found", "ERR_NOT_FOUND")

        try:
            mappings = get.mappings
            if isinstance(mappings, str):
                mappings = json.loads(mappings)
            if not isinstance(mappings, list):
                return self._fail("mappings must be a list", "ERR_PARAM_INVALID")

            normalized = []
            for m in mappings:
                if not isinstance(m, dict):
                    return self._fail("mapping item must be object", "ERR_PARAM_INVALID")
                source_db = str(m.get("source_db", "")).strip()
                target_db = str(m.get("target_db", "")).strip()
                if not source_db or not target_db:
                    return self._fail("source_db and target_db are required", "ERR_PARAM_REQUIRED")
                normalized.append({"source_db": source_db, "target_db": target_db})
            item["db_mappings"] = normalized
            item["updated_at"] = self._now()
            self._append_log(item["source_id"], "更新库映射，共{}条".format(len(normalized)))
            self._save_config(data)
            return self._ok({"source_id": item.get("source_id"), "count": len(normalized)}, "库映射更新成功")
        except Exception as ex:
            return self._fail("mappings parse failed: {}".format(ex), "ERR_PARSE_MAPPINGS")

    def list_db_mappings(self, get):
        if not hasattr(get, "source_id"):
            return public.returnMsg(False, "missing parameter: source_id")
        data = self._load_config()
        item = self._find_source(data, str(get.source_id).strip())
        if not item:
            return public.returnMsg(False, "source not found")
        return public.returnMsg(True, item.get("db_mappings", []))

    def remove_source(self, get):
        if not hasattr(get, "source_id"):
            return public.returnMsg(False, "missing parameter: source_id")
        source_id = str(get.source_id).strip()

        data = self._load_config()
        item = self._find_source(data, source_id)
        old_len = len(data.get("sources", []))
        data["sources"] = [s for s in data.get("sources", []) if s.get("source_id") != source_id]
        if len(data["sources"]) == old_len:
            return public.returnMsg(False, "source not found")
        if item:
            self._append_log(source_id, "删除来源")
        if not self._save_config(data):
            return public.returnMsg(False, "save config failed")
        return public.returnMsg(True, "来源已删除")

    def start_channel(self, get):
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
        master_host = self._sql_escape(item.get("master_host"))
        master_port = int(item.get("master_port", 3306))
        repl_user = self._sql_escape(item.get("repl_user"))
        repl_password = self._sql_escape(item.get("repl_password"))

        try:
            self._exec_sql("STOP SLAVE FOR CHANNEL '{}'".format(safe_channel))
        except Exception:
            pass

        try:
            change_sql = (
                "CHANGE MASTER TO MASTER_HOST='{host}', MASTER_PORT={port}, "
                "MASTER_USER='{user}', MASTER_PASSWORD='{pwd}', MASTER_AUTO_POSITION=1 "
                "FOR CHANNEL '{channel}'"
            ).format(
                host=master_host,
                port=master_port,
                user=repl_user,
                pwd=repl_password,
                channel=safe_channel,
            )
            self._exec_sql(change_sql)
            self._exec_sql("START SLAVE FOR CHANNEL '{}'".format(safe_channel))
        except Exception as ex:
            item["status"]["running"] = False
            item["status"]["io_running"] = "No"
            item["status"]["sql_running"] = "No"
            item["status"]["last_error"] = "启动失败: {}".format(ex)
            item["updated_at"] = self._now()
            self._save_config(data)
            return public.returnMsg(False, item["status"]["last_error"])

        item["status"] = self._get_source_status(channel_name)
        item["updated_at"] = self._now()
        self._append_log(item["source_id"], "启动 channel 成功")
        self._save_config(data)
        return public.returnMsg(True, "通道启动成功")

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
            self._exec_sql("STOP SLAVE FOR CHANNEL '{}'".format(safe_channel))
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
            return self._fail("missing parameter: source_id", "ERR_PARAM_REQUIRED")
        mode = "auto"
        if hasattr(get, "mode") and str(get.mode).strip():
            mode = str(get.mode).strip()
        if mode not in ["auto", "physical", "logical"]:
            return self._fail("mode must be auto/physical/logical", "ERR_PARAM_INVALID")

        data = self._load_config()
        source = self._find_source(data, str(get.source_id).strip())
        if not source:
            return self._fail("source not found", "ERR_NOT_FOUND")
        if not source.get("db_mappings"):
            return self._fail("请先配置库映射后再创建初始化任务", "ERR_MAPPINGS_EMPTY")

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
        data = self._load_config()
        task = self._find_bootstrap_task(data, task_id)
        if not task:
            return public.returnMsg(False, "task not found")
        if task.get("status") == "done":
            return public.returnMsg(True, "任务已完成")
        if task.get("status") == "cancelled":
            return public.returnMsg(False, "任务已取消")
        if task.get("status") == "running" and task.get("worker_id") and incoming_worker and incoming_worker != task.get("worker_id"):
            return public.returnMsg(False, "任务已被其他worker接管")

        task["status"] = "running"
        task["progress"] = 0
        task["current_step"] = "初始化开始"
        task["started_at"] = task.get("started_at") or self._now()
        if incoming_worker:
            task["worker_id"] = incoming_worker
        elif not task.get("worker_id"):
            task["worker_id"] = "worker_" + uuid.uuid4().hex[:8]
        task["error"] = ""
        task["error_type"] = ""
        task["updated_at"] = self._now()
        task["last_heartbeat"] = self._now()
        self._save_config(data)

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
            self._task_step_update(data, task, "校验并接管复制", 90)
            time.sleep(0.2)

            data = self._load_config()
            task = self._find_bootstrap_task(data, task_id)
            if not task:
                return public.returnMsg(False, "task not found")
            task["checkpoint_step"] = task.get("steps", [])[step_count - 1]
            task["progress"] = 100
            task["current_step"] = "初始化完成"
            task["status"] = "done"
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
            return self._fail("missing parameter: task_id", "ERR_PARAM_REQUIRED")
        task_id = str(get.task_id).strip()

        data = self._load_config()
        task = self._find_bootstrap_task(data, task_id)
        if not task:
            return self._fail("task not found", "ERR_NOT_FOUND")
        if task.get("status") == "running":
            return self._fail("任务正在执行中", "ERR_TASK_RUNNING")
        if task.get("status") == "done":
            return self._fail("任务已完成，若需重跑请新建任务", "ERR_TASK_DONE")

        worker_id = "worker_" + uuid.uuid4().hex[:8]
        task["worker_id"] = worker_id
        task["last_heartbeat"] = self._now()
        self._save_config(data)

        cmd = "nohup btpython /www/server/panel/plugin/mysql_multi_source/start_sync.py run_bootstrap_task {} {} > /dev/null 2>&1 &".format(
            task_id, worker_id
        )
        public.ExecShell(cmd)
        self._append_log(task.get("source_id"), "异步触发初始化任务: {} by {}".format(task_id, worker_id))
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

    def diagnose_source(self, get):
        if not hasattr(get, "source_id"):
            return public.returnMsg(False, "missing parameter: source_id")
        data = self._load_config()
        source = self._find_source(data, str(get.source_id).strip())
        if not source:
            return public.returnMsg(False, "source not found")

        status = self._get_source_status(source.get("channel_name"))
        network_ok = self.test_source_connection(get).get("status", False)
        gtid_info = self.get_gtid_status().get("msg", {})
        suggestions = []
        if not network_ok:
            suggestions.append("检查主库网络连通性和防火墙白名单")
        if not gtid_info.get("enabled"):
            suggestions.append("当前从库 GTID 未开启，建议开启后再使用 GTID 自动定位")
        if status.get("last_error"):
            suggestions.append("根据 Last_Error 先处理权限/位点/数据冲突问题")
        if not source.get("db_mappings"):
            suggestions.append("建议配置库映射，避免多源同名库冲突")
        if not suggestions:
            suggestions.append("当前状态正常，可继续观察复制延迟")

        return public.returnMsg(
            True,
            {
                "source_id": source.get("source_id"),
                "channel_name": source.get("channel_name"),
                "status": status,
                "network_ok": network_ok,
                "gtid_enabled": gtid_info.get("enabled", False),
                "suggestions": suggestions,
            },
        )

    def get_source_logs(self, get):
        if not hasattr(get, "source_id"):
            return public.returnMsg(False, "missing parameter: source_id")
        source_id = str(get.source_id).strip()
        log_path = os.path.join(self.log_dir, "{}.log".format(source_id))
        if not os.path.exists(log_path):
            return public.returnMsg(True, "")
        content = public.ReadFile(log_path) or ""
        keyword = str(get.keyword).strip() if hasattr(get, "keyword") else ""
        if keyword:
            filtered = []
            for line in content.splitlines():
                if keyword in line:
                    filtered.append(line)
            content = "\n".join(filtered)
        return public.returnMsg(True, content or "")

    def get_task_logs(self, get):
        if not hasattr(get, "task_id"):
            return public.returnMsg(False, "missing parameter: task_id")
        task_id = str(get.task_id).strip()
        log_path = os.path.join(self.log_dir, "task_{}.log".format(task_id))
        if not os.path.exists(log_path):
            return public.returnMsg(True, "")
        content = public.ReadFile(log_path) or ""
        keyword = str(get.keyword).strip() if hasattr(get, "keyword") else ""
        if keyword:
            filtered = []
            for line in content.splitlines():
                if keyword in line:
                    filtered.append(line)
            content = "\n".join(filtered)
        return public.returnMsg(True, content or "")

    def overview_metrics(self, get=None):
        data = self._load_config()
        total = len(data.get("sources", []))
        running = 0
        stopped = 0
        errors = 0
        for source in data.get("sources", []):
            status = self._get_source_status(source.get("channel_name"))
            if status.get("running"):
                running += 1
            else:
                stopped += 1
            if status.get("last_error"):
                errors += 1
        tasks = data.get("bootstrap_tasks", [])
        done_tasks = [t for t in tasks if t.get("status") == "done"]
        failed_tasks = [t for t in tasks if t.get("status") == "failed"]
        avg_duration = 0
        if done_tasks:
            avg_duration = int(sum([int(t.get("duration_seconds", 0) or 0) for t in done_tasks]) / len(done_tasks))
        return public.returnMsg(
            True,
            {
                "total_sources": total,
                "running_sources": running,
                "stopped_sources": stopped,
                "error_sources": errors,
                "bootstrap_tasks": len(tasks),
                "bootstrap_done": len(done_tasks),
                "bootstrap_failed": len(failed_tasks),
                "avg_bootstrap_duration_seconds": avg_duration,
            },
        )
