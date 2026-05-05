# coding: utf-8
"""Tests for mms/diagnose_service.py"""

import json
import os
import types
from unittest.mock import patch, MagicMock

import pytest


def _make_get(**kwargs):
    return types.SimpleNamespace(**kwargs)


# ---------------------------------------------------------------------------
# Sample config fixtures
# ---------------------------------------------------------------------------

def _sample_config(sources=None, tasks=None, mode="replica_mode"):
    return {
        "mode": mode,
        "sources": sources or [],
        "bootstrap_tasks": tasks or [],
    }


def _sample_source(source_id="s1", channel_name="ch1", master_host="10.0.0.1",
                    repl_user="repl", repl_password="", db_mappings=None):
    return {
        "source_id": source_id,
        "channel_name": channel_name,
        "master_host": master_host,
        "master_port": 3306,
        "repl_user": repl_user,
        "repl_password": repl_password,
        "db_mappings": db_mappings or [{"source_db": "db1", "target_db": "db1"}],
        "created_at": 1000,
        "updated_at": 1000,
    }


def _sample_task(task_id="t1", source_id="s1", status="done", error=""):
    return {
        "task_id": task_id,
        "source_id": source_id,
        "status": status,
        "error": error,
        "error_type": "",
        "duration_seconds": 60,
    }


def _slave_status_row(channel="ch1", io_running="Yes", sql_running="Yes",
                       seconds_behind=0, last_error=""):
    return {
        "Channel_Name": channel,
        "Slave_IO_Running": io_running,
        "Slave_SQL_Running": sql_running,
        "Seconds_Behind_Master": seconds_behind,
        "Last_Error": last_error,
    }


# ===========================================================================
# Tests for _classify_error
# ===========================================================================

class TestClassifyError:
    """_classify_error: categorize error messages."""

    def test_access_denied(self, plugin):
        assert plugin._classify_error("Access denied for user") == "权限问题"

    def test_permission(self, plugin):
        assert plugin._classify_error("permission denied") == "权限问题"

    def test_connection_timeout(self, plugin):
        assert plugin._classify_error("Connection timed out") == "网络问题"

    def test_network_error(self, plugin):
        assert plugin._classify_error("network unreachable") == "网络问题"

    def test_gtid_error(self, plugin):
        assert plugin._classify_error("GTID not enabled") == "GTID问题"

    def test_duplicate_error(self, plugin):
        assert plugin._classify_error("Duplicate entry for key") == "数据冲突"

    def test_conflict(self, plugin):
        assert plugin._classify_error("conflict detected") == "数据冲突"

    def test_disk_space(self, plugin):
        assert plugin._classify_error("No space left on device") == "资源不足"

    def test_memory(self, plugin):
        assert plugin._classify_error("Out of memory") == "资源不足"

    def test_unknown(self, plugin):
        assert plugin._classify_error("something random") == "未知问题"

    def test_none_input(self, plugin):
        assert plugin._classify_error(None) == "未知问题"

    def test_empty_input(self, plugin):
        assert plugin._classify_error("") == "未知问题"


# ===========================================================================
# Tests for _classify_connectivity_error
# ===========================================================================

class TestClassifyConnectivityError:
    """_classify_connectivity_error: categorize connection errors."""

    def test_timed_out(self, plugin):
        assert plugin._classify_connectivity_error("connect timed out") == "网络超时"

    def test_refused(self, plugin):
        assert plugin._classify_connectivity_error("Connection refused") == "端口拒绝"

    def test_no_route(self, plugin):
        assert plugin._classify_connectivity_error("No route to host") == "路由不可达"

    def test_unreachable(self, plugin):
        assert plugin._classify_connectivity_error("Network unreachable") == "路由不可达"

    def test_access_denied(self, plugin):
        assert plugin._classify_connectivity_error("Access denied") == "账号或权限错误"

    def test_unknown(self, plugin):
        assert plugin._classify_connectivity_error("bizarre error") == "未知连接错误"

    def test_none_input(self, plugin):
        assert plugin._classify_connectivity_error(None) == "未知连接错误"


# ===========================================================================
# Tests for diagnose_source
# ===========================================================================

class TestDiagnoseSource:
    """diagnose_source: per-source diagnosis with status, network, GTID checks."""

    def _setup_plugin(self, plugin, source=None, status=None, network_ok=True,
                      gtid_enabled=True):
        """Wire up mocks for diagnose_source dependencies."""
        src = source or _sample_source()
        cfg = _sample_config(sources=[src])
        plugin._load_config = lambda: cfg

        plugin._get_source_status = lambda ch: status or {
            "running": True, "io_running": "Yes", "sql_running": "Yes",
            "seconds_behind": 0, "last_error": "",
        }
        plugin.test_source_connection = lambda get: {"status": network_ok}
        plugin.get_gtid_status = lambda: {
            "status": True,
            "msg": {"enabled": gtid_enabled},
        }
        return src

    def test_healthy_source(self, plugin):
        """Healthy source with network OK and GTID enabled => no actionable issues."""
        self._setup_plugin(plugin)
        get = _make_get(source_id="s1")
        result = plugin.diagnose_source(get)
        assert result["status"] is True
        msg = result["msg"]
        assert msg["source_id"] == "s1"
        assert msg["network_ok"] is True
        assert msg["gtid_enabled"] is True
        assert "当前状态正常" in msg["suggestions"][-1]

    def test_network_down(self, plugin):
        """Network unreachable => suggestion to check firewall."""
        self._setup_plugin(plugin, network_ok=False)
        get = _make_get(source_id="s1")
        result = plugin.diagnose_source(get)
        msg = result["msg"]
        assert msg["network_ok"] is False
        assert any("网络连通性" in s for s in msg["suggestions"])

    def test_gtid_disabled(self, plugin):
        """GTID disabled => suggestion to enable GTID."""
        self._setup_plugin(plugin, gtid_enabled=False)
        get = _make_get(source_id="s1")
        result = plugin.diagnose_source(get)
        msg = result["msg"]
        assert msg["gtid_enabled"] is False
        assert any("GTID" in s for s in msg["suggestions"])

    def test_replication_error(self, plugin):
        """Last_Error present => suggestion to fix error."""
        status = {
            "running": False, "io_running": "No", "sql_running": "No",
            "seconds_behind": None, "last_error": "Slave I/O stopped",
        }
        self._setup_plugin(plugin, status=status)
        get = _make_get(source_id="s1")
        result = plugin.diagnose_source(get)
        msg = result["msg"]
        assert msg["status"]["last_error"] == "Slave I/O stopped"
        assert any("Last_Error" in s for s in msg["suggestions"])

    def test_no_db_mappings(self, plugin):
        """No db_mappings => suggestion to configure mappings."""
        src = _sample_source()
        src["db_mappings"] = []
        self._setup_plugin(plugin, source=src)
        get = _make_get(source_id="s1")
        result = plugin.diagnose_source(get)
        msg = result["msg"]
        assert any("映射" in s for s in msg["suggestions"])

    def test_multiple_issues(self, plugin):
        """Multiple issues => multiple suggestions."""
        status = {
            "running": False, "io_running": "No", "sql_running": "No",
            "seconds_behind": None, "last_error": "Access denied",
        }
        self._setup_plugin(plugin, status=status,
                           network_ok=False, gtid_enabled=False)
        get = _make_get(source_id="s1")
        result = plugin.diagnose_source(get)
        suggestions = result["msg"]["suggestions"]
        # 3 suggestions: network, GTID, Last_Error
        assert len(suggestions) >= 3
        assert any("网络" in s for s in suggestions)
        assert any("GTID" in s for s in suggestions)
        assert any("Last_Error" in s for s in suggestions)

    def test_missing_source_id(self, plugin):
        """Missing source_id parameter."""
        get = types.SimpleNamespace()  # no source_id
        result = plugin.diagnose_source(get)
        assert result["status"] is False

    def test_source_not_found(self, plugin):
        """source_id not in config."""
        plugin._load_config = lambda: _sample_config(sources=[])
        get = _make_get(source_id="nonexistent")
        result = plugin.diagnose_source(get)
        assert result["status"] is False


# ===========================================================================
# Tests for wizard_diagnose_all
# ===========================================================================

class TestWizardDiagnoseAll:
    """wizard_diagnose_all: classify issues across all sources and tasks."""

    def _setup_plugin(self, plugin, sources=None, tasks=None,
                      slave_status=None, health_check=None):
        """Wire up mocks for wizard_diagnose_all dependencies."""
        cfg = _sample_config(sources=sources or [], tasks=tasks or [])
        plugin._load_config = lambda: cfg
        plugin._all_slave_status = lambda: slave_status or {}
        plugin.master_health_check = lambda: health_check or {
            "status": True,
            "msg": {"summary": {"pass": 5, "fail": 0}, "items": []},
        }

    def test_all_healthy(self, plugin):
        """All sources healthy, no failed tasks => zero issues."""
        sources = [_sample_source("s1", "ch1"), _sample_source("s2", "ch2")]
        slave_status = {
            "ch1": _slave_status_row("ch1"),
            "ch2": _slave_status_row("ch2"),
        }
        self._setup_plugin(plugin, sources=sources, slave_status=slave_status)
        result = plugin.wizard_diagnose_all()
        assert result["status"] is True
        msg = result["msg"]
        assert msg["total_issues"] == 0
        for group in msg["groups"].values():
            assert len(group) == 0

    def test_one_source_with_error(self, plugin):
        """One source has replication error => classified into correct group."""
        sources = [_sample_source("s1", "ch1")]
        slave_status = {
            "ch1": _slave_status_row("ch1", io_running="No", last_error="Access denied for user"),
        }
        self._setup_plugin(plugin, sources=sources, slave_status=slave_status)
        result = plugin.wizard_diagnose_all()
        msg = result["msg"]
        assert msg["total_issues"] >= 1
        assert len(msg["groups"]["auth"]) >= 1
        assert msg["groups"]["auth"][0]["category"] == "权限问题"

    def test_network_error_classification(self, plugin):
        """Network error source => classified into network group."""
        sources = [_sample_source("s1", "ch1")]
        slave_status = {
            "ch1": _slave_status_row("ch1", io_running="No",
                                      last_error="Connection timed out"),
        }
        self._setup_plugin(plugin, sources=sources, slave_status=slave_status)
        result = plugin.wizard_diagnose_all()
        msg = result["msg"]
        assert len(msg["groups"]["network"]) >= 1
        assert msg["groups"]["network"][0]["fixable"] is True

    def test_failed_task_classification(self, plugin):
        """Failed bootstrap task => classified by error type."""
        tasks = [_sample_task("t1", "s1", status="failed", error="Duplicate entry for key")]
        self._setup_plugin(plugin, tasks=tasks)
        result = plugin.wizard_diagnose_all()
        msg = result["msg"]
        assert msg["total_issues"] >= 1
        assert len(msg["groups"]["conflict"]) >= 1

    def test_health_check_fail_items(self, plugin):
        """Health check failures => config group."""
        health_check = {
            "status": True,
            "msg": {
                "summary": {"pass": 4, "fail": 1},
                "items": [
                    {"name": "gtid_mode", "status": "fail", "current": "OFF",
                     "expected": "ON", "suggestion": "开启 GTID"},
                ],
            },
        }
        self._setup_plugin(plugin, health_check=health_check)
        result = plugin.wizard_diagnose_all()
        msg = result["msg"]
        assert len(msg["groups"]["config"]) >= 1
        assert msg["groups"]["config"][0]["fixable"] is True

    def test_health_check_exception(self, plugin):
        """Health check raises exception => graceful degradation."""
        self._setup_plugin(plugin)
        plugin.master_health_check = lambda: (_ for _ in ()).throw(Exception("unavailable"))
        result = plugin.wizard_diagnose_all()
        assert result["status"] is True
        # config group should be empty since health check failed
        assert len(result["msg"]["groups"]["config"]) == 0

    def test_mixed_sources_and_tasks(self, plugin):
        """Mix of healthy sources, errored sources, and failed tasks."""
        sources = [
            _sample_source("s1", "ch1"),
            _sample_source("s2", "ch2"),
            _sample_source("s3", "ch3"),
        ]
        slave_status = {
            "ch1": _slave_status_row("ch1"),  # healthy
            "ch2": _slave_status_row("ch2", io_running="No",
                                      last_error="GTID not enabled"),
            # ch3 has no status row => treated as not running
        }
        tasks = [
            _sample_task("t1", "s1", status="done"),
            _sample_task("t2", "s2", status="failed", error="No space left on device"),
        ]
        self._setup_plugin(plugin, sources=sources, tasks=tasks,
                           slave_status=slave_status)
        result = plugin.wizard_diagnose_all()
        msg = result["msg"]
        # ch2 has GTID error, ch3 has no status, t2 has resource error
        assert msg["total_issues"] >= 2
        assert len(msg["groups"]["gtid"]) >= 1
        assert len(msg["groups"]["resource"]) >= 1

    def test_empty_config(self, plugin):
        """Empty config => zero issues, no errors."""
        self._setup_plugin(plugin)
        result = plugin.wizard_diagnose_all()
        assert result["status"] is True
        assert result["msg"]["total_issues"] == 0

    def test_fixable_flag(self, plugin):
        """Verify fixable flags for different error categories."""
        sources = [
            _sample_source("s1", "ch1"),
            _sample_source("s2", "ch2"),
        ]
        slave_status = {
            "ch1": _slave_status_row("ch1", io_running="No",
                                      last_error="Access denied"),
            "ch2": _slave_status_row("ch2", io_running="No",
                                      last_error="conflict detected"),
        }
        self._setup_plugin(plugin, sources=sources, slave_status=slave_status)
        result = plugin.wizard_diagnose_all()
        msg = result["msg"]
        auth_items = msg["groups"]["auth"]
        conflict_items = msg["groups"]["conflict"]
        assert auth_items[0]["fixable"] is True
        assert conflict_items[0]["fixable"] is False


# ===========================================================================
# Tests for wizard_quick_fix
# ===========================================================================

class TestWizardQuickFix:
    """wizard_quick_fix: one-click fix dispatcher."""

    def test_config_category_delegates_to_auto_fix(self, plugin):
        """config/gtid category => delegates to master_auto_fix_apply."""
        plugin.master_auto_fix_apply = lambda get: {
            "status": True, "msg": {"message": "fixed", "code": "FIXED"},
        }
        get = _make_get(category="config")
        result = plugin.wizard_quick_fix(get)
        assert result["status"] is True

    def test_gtid_category_delegates_to_auto_fix(self, plugin):
        """gtid category => delegates to master_auto_fix_apply."""
        plugin.master_auto_fix_apply = lambda get: {
            "status": True, "msg": {"message": "fixed", "code": "FIXED"},
        }
        get = _make_get(category="gtid")
        result = plugin.wizard_quick_fix(get)
        assert result["status"] is True

    def test_task_category_delegates_to_recover(self, plugin):
        """task/stuck_tasks category => delegates to recover_bootstrap_tasks."""
        plugin.recover_bootstrap_tasks = lambda: {
            "status": True, "msg": {"message": "recovered", "code": "RECOVERED"},
        }
        get = _make_get(category="task")
        result = plugin.wizard_quick_fix(get)
        assert result["status"] is True

    def test_stuck_tasks_category(self, plugin):
        """stuck_tasks category => delegates to recover_bootstrap_tasks."""
        plugin.recover_bootstrap_tasks = lambda: {
            "status": True, "msg": {"message": "recovered", "code": "RECOVERED"},
        }
        get = _make_get(category="stuck_tasks")
        result = plugin.wizard_quick_fix(get)
        assert result["status"] is True

    def test_network_category_returns_hint(self, plugin):
        """network category => returns self-service hint."""
        get = _make_get(category="network")
        result = plugin.wizard_quick_fix(get)
        assert result["status"] is True
        assert "hint" in result["msg"]

    def test_auth_category_returns_hint(self, plugin):
        """auth category => returns self-service hint."""
        get = _make_get(category="auth")
        result = plugin.wizard_quick_fix(get)
        assert result["status"] is True
        assert "hint" in result["msg"]

    def test_unknown_category_returns_fail(self, plugin):
        """Unknown category => returns failure with error code."""
        get = _make_get(category="nonexistent")
        result = plugin.wizard_quick_fix(get)
        assert result["status"] is False
        assert result["msg"]["code"] == "ERR_UNKNOWN_CATEGORY"

    def test_missing_category_param(self, plugin):
        """Missing category parameter => returns failure."""
        get = types.SimpleNamespace()  # no category
        result = plugin.wizard_quick_fix(get)
        assert result["status"] is False
        assert "ERR_PARAM_REQUIRED" in result["msg"]["code"]

    def test_auto_fix_failure_propagated(self, plugin):
        """master_auto_fix_apply failure => propagated to caller."""
        plugin.master_auto_fix_apply = lambda get: {
            "status": False, "msg": {"message": "fix failed", "code": "FIX_FAILED"},
        }
        get = _make_get(category="config")
        result = plugin.wizard_quick_fix(get)
        assert result["status"] is False

    def test_recover_failure_propagated(self, plugin):
        """recover_bootstrap_tasks failure => propagated to caller."""
        plugin.recover_bootstrap_tasks = lambda: {
            "status": False, "msg": {"message": "recover failed", "code": "RECOVER_FAILED"},
        }
        get = _make_get(category="task")
        result = plugin.wizard_quick_fix(get)
        assert result["status"] is False

    def test_category_case_insensitive(self, plugin):
        """Category matching is case-insensitive."""
        plugin.master_auto_fix_apply = lambda get: {
            "status": True, "msg": {"message": "fixed", "code": "FIXED"},
        }
        get = _make_get(category="CONFIG")
        result = plugin.wizard_quick_fix(get)
        assert result["status"] is True
