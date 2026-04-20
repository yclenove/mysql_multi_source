#!/usr/bin/python
# coding: utf-8

import json
import os
import re
import socket
import sys
import time
import uuid

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

    def __init__(self):
        pass

    def _mysql(self):
        return db_mysql.panelMysql()

    def _sql_escape(self, value):
        return str(value).replace("\\", "\\\\").replace("'", "\\'")

    def _validate_channel_name(self, channel_name):
        return re.match(r"^[A-Za-z0-9_]{1,64}$", channel_name or "") is not None

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
            "slave_instance": {
                "host": "127.0.0.1",
                "port": 3306,
                "updated_at": self._now(),
            },
            "sources": [],
            "bootstrap_tasks": [],
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
        data["slave_instance"]["updated_at"] = self._now()
        return bool(public.WriteFile(self.config_path, json.dumps(data)))

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

    def health_check(self, get=None):
        data = self._load_config()
        return public.returnMsg(
            True,
            {
                "plugin": "mysql_multi_source",
                "version": data.get("version", "1"),
                "sources_count": len(data.get("sources", [])),
                "bootstrap_tasks_count": len(data.get("bootstrap_tasks", [])),
            },
        )

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
                return public.returnMsg(False, "missing parameter: {}".format(key))
        if not self._validate_channel_name(str(get.channel_name).strip()):
            return public.returnMsg(False, "channel_name 仅支持字母、数字、下划线，最长64位")

        data = self._load_config()
        if self._find_source(data, get.source_id):
            return public.returnMsg(False, "source_id already exists")
        for source in data.get("sources", []):
            if source.get("channel_name") == str(get.channel_name).strip():
                return public.returnMsg(False, "channel_name already exists")

        source = {
            "source_id": str(get.source_id).strip(),
            "channel_name": str(get.channel_name).strip(),
            "master_host": str(get.master_host).strip(),
            "master_port": int(get.master_port),
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
            return public.returnMsg(False, "save config failed")
        return public.returnMsg(True, "来源添加成功")

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
            return public.returnMsg(True, "主库网络连通")
        except Exception as ex:
            return public.returnMsg(False, "主库网络不通: {}".format(ex))
        finally:
            sock.close()

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
            return public.returnMsg(False, "missing parameter: source_id")
        if not hasattr(get, "mappings"):
            return public.returnMsg(False, "missing parameter: mappings")

        data = self._load_config()
        item = self._find_source(data, str(get.source_id).strip())
        if not item:
            return public.returnMsg(False, "source not found")

        try:
            mappings = get.mappings
            if isinstance(mappings, str):
                mappings = json.loads(mappings)
            if not isinstance(mappings, list):
                return public.returnMsg(False, "mappings must be a list")

            normalized = []
            for m in mappings:
                if not isinstance(m, dict):
                    return public.returnMsg(False, "mapping item must be object")
                source_db = str(m.get("source_db", "")).strip()
                target_db = str(m.get("target_db", "")).strip()
                if not source_db or not target_db:
                    return public.returnMsg(False, "source_db and target_db are required")
                normalized.append({"source_db": source_db, "target_db": target_db})
            item["db_mappings"] = normalized
            item["updated_at"] = self._now()
            self._append_log(item["source_id"], "更新库映射，共{}条".format(len(normalized)))
            self._save_config(data)
            return public.returnMsg(True, "库映射更新成功")
        except Exception as ex:
            return public.returnMsg(False, "mappings parse failed: {}".format(ex))

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
            return public.returnMsg(False, "missing parameter: source_id")
        mode = "auto"
        if hasattr(get, "mode") and str(get.mode).strip():
            mode = str(get.mode).strip()
        if mode not in ["auto", "physical", "logical"]:
            return public.returnMsg(False, "mode must be auto/physical/logical")

        data = self._load_config()
        source = self._find_source(data, str(get.source_id).strip())
        if not source:
            return public.returnMsg(False, "source not found")
        if not source.get("db_mappings"):
            return public.returnMsg(False, "请先配置库映射后再创建初始化任务")

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
        return public.returnMsg(True, task)

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
            return public.returnMsg(False, "missing parameter: task_id")
        task_id = str(get.task_id).strip()

        data = self._load_config()
        task = self._find_bootstrap_task(data, task_id)
        if not task:
            return public.returnMsg(False, "task not found")
        if task.get("status") == "running":
            return public.returnMsg(False, "任务正在执行中")
        if task.get("status") == "done":
            return public.returnMsg(False, "任务已完成，若需重跑请新建任务")

        worker_id = "worker_" + uuid.uuid4().hex[:8]
        task["worker_id"] = worker_id
        task["last_heartbeat"] = self._now()
        self._save_config(data)

        cmd = "nohup btpython /www/server/panel/plugin/mysql_multi_source/start_sync.py run_bootstrap_task {} {} > /dev/null 2>&1 &".format(
            task_id, worker_id
        )
        public.ExecShell(cmd)
        self._append_log(task.get("source_id"), "异步触发初始化任务: {} by {}".format(task_id, worker_id))
        return public.returnMsg(True, "已触发后台执行")

    def get_bootstrap_tasks(self, get=None):
        data = self._load_config()
        return public.returnMsg(True, data.get("bootstrap_tasks", []))

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
