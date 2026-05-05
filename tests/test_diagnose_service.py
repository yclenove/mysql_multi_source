# coding: utf-8
"""Tests for mms/diagnose_service.py"""

import types

import pytest


def _make_get(**kwargs):
    return types.SimpleNamespace(**kwargs)


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
