# coding: utf-8
"""Tests for mms/dashboard_service.py"""

import os
import types

import pytest


def _make_get(**kwargs):
    return types.SimpleNamespace(**kwargs)


class TestMapStatusRow:
    """_map_status_row: normalize replication status rows."""

    def test_dict_with_slave_keys(self, plugin):
        row = {
            "Slave_IO_Running": "Yes",
            "Slave_SQL_Running": "Yes",
            "Seconds_Behind_Master": 5,
            "Last_Error": "",
        }
        result = plugin._map_status_row(row)
        assert result["running"] is True
        assert result["io_running"] == "Yes"
        assert result["sql_running"] == "Yes"
        assert result["seconds_behind"] == 5
        assert result["last_error"] == ""

    def test_dict_with_replica_keys(self, plugin):
        row = {
            "Replica_IO_Running": "Yes",
            "Replica_SQL_Running": "No",
            "Seconds_Behind_Source": 10,
            "Last_IO_Error": "connection lost",
        }
        result = plugin._map_status_row(row)
        assert result["running"] is False
        assert result["io_running"] == "Yes"
        assert result["sql_running"] == "No"
        assert result["last_error"] == "connection lost"

    def test_non_dict_input(self, plugin):
        result = plugin._map_status_row(None)
        assert result["running"] is False
        assert result["io_running"] == "No"

    def test_empty_dict(self, plugin):
        result = plugin._map_status_row({})
        assert result["running"] is False

    def test_io_running_no_sql_running_yes(self, plugin):
        row = {"Slave_IO_Running": "No", "Slave_SQL_Running": "Yes"}
        result = plugin._map_status_row(row)
        assert result["running"] is False

    def test_both_running(self, plugin):
        row = {"Slave_IO_Running": "Yes", "Slave_SQL_Running": "Yes"}
        result = plugin._map_status_row(row)
        assert result["running"] is True


class TestAllSlaveStatus:
    """_all_slave_status: fetch and index slave status by channel."""

    def test_returns_dict(self, plugin):
        # Mock _query_sql to return rows with channel names
        plugin._query_sql = lambda sql: [
            {"Channel_Name": "ch1", "Slave_IO_Running": "Yes", "Slave_SQL_Running": "Yes"},
            {"Channel_Name": "ch2", "Slave_IO_Running": "No", "Slave_SQL_Running": "No"},
        ]
        result = plugin._all_slave_status()
        assert "ch1" in result
        assert "ch2" in result
        assert result["ch1"]["Slave_IO_Running"] == "Yes"

    def test_empty_result(self, plugin):
        plugin._query_sql = lambda sql: []
        result = plugin._all_slave_status()
        assert result == {}

    def test_exception_handling(self, plugin):
        plugin._query_sql = lambda sql: (_ for _ in ()).throw(Exception("db error"))
        result = plugin._all_slave_status()
        assert result == {}

    def test_non_list_fallback(self, plugin):
        """If SHOW SLAVE STATUS returns non-list, try SHOW REPLICA STATUS."""
        call_count = {"n": 0}
        def mock_query(sql):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "not a list"
            return [{"Channel_Name": "ch1", "Replica_IO_Running": "Yes", "Replica_SQL_Running": "Yes"}]
        plugin._query_sql = mock_query
        result = plugin._all_slave_status()
        assert "ch1" in result


class TestSourceDetail:
    """source_detail: get source info with masked password."""

    def test_success(self, plugin):
        plugin._ensure_dirs()
        data = plugin._default_config()
        data["sources"].append({
            "source_id": "s1",
            "channel_name": "ch1",
            "repl_password": plugin._crypto_encrypt("mypassword"),
        })
        plugin._save_config(data)
        result = plugin.source_detail(_make_get(source_id="s1"))
        assert result["status"] is True
        assert result["msg"]["source_id"] == "s1"
        # Password should be masked, not plaintext
        assert "mypassword" not in str(result["msg"].get("repl_password", ""))

    def test_not_found(self, plugin):
        plugin._ensure_dirs()
        plugin._save_config(plugin._default_config())
        result = plugin.source_detail(_make_get(source_id="nonexist"))
        assert result["status"] is False

    def test_missing_param(self, plugin):
        result = plugin.source_detail(_make_get())
        assert result["status"] is False


class TestGetSourceLogs:
    """get_source_logs: read log file with optional keyword filter."""

    def test_no_log_file(self, plugin):
        plugin._ensure_dirs()
        result = plugin.get_source_logs(_make_get(source_id="nonexist"))
        assert result["status"] is True
        assert result["msg"] == ""

    def test_with_log_content(self, plugin):
        plugin._ensure_dirs()
        log_path = os.path.join(plugin.log_dir, "src1.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("[2026-01-01 00:00:00] started\n[2026-01-01 00:01:00] error happened\n")
        result = plugin.get_source_logs(_make_get(source_id="src1"))
        assert result["status"] is True
        assert "started" in result["msg"]

    def test_with_keyword_filter(self, plugin):
        plugin._ensure_dirs()
        log_path = os.path.join(plugin.log_dir, "src1.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("[2026-01-01] started\n[2026-01-02] error happened\n")
        result = plugin.get_source_logs(_make_get(source_id="src1", keyword="error"))
        assert "error" in result["msg"]
        assert "started" not in result["msg"]

    def test_missing_param(self, plugin):
        result = plugin.get_source_logs(_make_get())
        assert result["status"] is False


class TestGetTaskLogs:
    """get_task_logs: read task log file."""

    def test_no_log_file(self, plugin):
        plugin._ensure_dirs()
        result = plugin.get_task_logs(_make_get(task_id="task_001"))
        assert result["status"] is True
        assert result["msg"] == ""

    def test_with_log_content(self, plugin):
        plugin._ensure_dirs()
        log_path = os.path.join(plugin.log_dir, "task_task_001.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("[2026-01-01] step 1 done\n")
        result = plugin.get_task_logs(_make_get(task_id="task_001"))
        assert "step 1 done" in result["msg"]

    def test_missing_param(self, plugin):
        result = plugin.get_task_logs(_make_get())
        assert result["status"] is False


class TestOverviewMetrics:
    """overview_metrics: aggregate source and task statistics."""

    def test_empty_config(self, plugin):
        plugin._ensure_dirs()
        plugin._save_config(plugin._default_config())
        plugin._query_sql = lambda sql: []
        result = plugin.overview_metrics()
        assert result["status"] is True
        assert result["msg"]["total_sources"] == 0
        assert result["msg"]["running_sources"] == 0

    def test_with_sources(self, plugin):
        plugin._ensure_dirs()
        data = plugin._default_config()
        data["sources"] = [
            {"source_id": "s1", "channel_name": "ch1"},
            {"source_id": "s2", "channel_name": "ch2"},
        ]
        data["bootstrap_tasks"] = [
            {"task_id": "t1", "status": "done", "duration_seconds": 10},
            {"task_id": "t2", "status": "failed"},
        ]
        plugin._save_config(data)
        plugin._query_sql = lambda sql: []
        result = plugin.overview_metrics()
        assert result["msg"]["total_sources"] == 2
        assert result["msg"]["bootstrap_tasks"] == 2
        assert result["msg"]["bootstrap_done"] == 1
        assert result["msg"]["bootstrap_failed"] == 1


class TestWizardDashboardSnapshot:
    """wizard_dashboard_snapshot: full dashboard data."""

    def test_snapshot_structure(self, plugin):
        plugin._ensure_dirs()
        data = plugin._default_config()
        data["sources"] = [
            {"source_id": "s1", "channel_name": "ch1", "master_host": "10.0.0.1",
             "master_port": 3306, "repl_user": "repl",
             "repl_password": plugin._crypto_encrypt("pwd")},
        ]
        plugin._save_config(data)
        plugin._query_sql = lambda sql: [
            {"Channel_Name": "ch1", "Slave_IO_Running": "Yes",
             "Slave_SQL_Running": "Yes", "Seconds_Behind_Master": 0,
             "Last_Error": ""},
        ]
        result = plugin.wizard_dashboard_snapshot()
        assert result["status"] is True
        msg = result["msg"]
        assert len(msg["sources"]) == 1
        assert msg["sources"][0]["source_id"] == "s1"
        assert "repl_password_masked" in msg["sources"][0]
        assert msg["metrics"]["total_sources"] == 1
        assert msg["metrics"]["running_sources"] == 1
