#!/usr/bin/python
# coding: utf-8

import json
import os
import re
import socket
import sys
import time

if "/www/server/panel/class" not in sys.path:
    sys.path.insert(0, "/www/server/panel/class")
if "/www/server/panel" not in sys.path:
    sys.path.insert(0, "/www/server/panel")

import public
import db_mysql


class mysql_multi_source_main:
    config_path = "/www/server/panel/plugin/mysql_multi_source/multi_source_info.json"
    log_dir = "/www/server/panel/plugin/mysql_multi_source/log"

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
        }

    def _ensure_dirs(self):
        plugin_dir = os.path.dirname(self.config_path)
        if not os.path.exists(plugin_dir):
            os.makedirs(plugin_dir)
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)

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

    def health_check(self, get=None):
        data = self._load_config()
        return public.returnMsg(
            True,
            {
                "plugin": "mysql_multi_source",
                "version": data.get("version", "1"),
                "sources_count": len(data.get("sources", [])),
            },
        )

    def list_sources(self, get=None):
        data = self._load_config()
        return public.returnMsg(True, data.get("sources", []))

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

        if not self._save_config(data):
            return public.returnMsg(False, "save config failed")
        return public.returnMsg(True, "source added")

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

    def remove_source(self, get):
        if not hasattr(get, "source_id"):
            return public.returnMsg(False, "missing parameter: source_id")
        source_id = str(get.source_id).strip()

        data = self._load_config()
        old_len = len(data.get("sources", []))
        data["sources"] = [s for s in data.get("sources", []) if s.get("source_id") != source_id]
        if len(data["sources"]) == old_len:
            return public.returnMsg(False, "source not found")
        if not self._save_config(data):
            return public.returnMsg(False, "save config failed")
        return public.returnMsg(True, "source removed")

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
        self._save_config(data)
        return public.returnMsg(True, "channel started")

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
        self._save_config(data)
        return public.returnMsg(True, "channel stopped")

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
