# coding: utf-8
"""Tests for mms/logging_audit.py"""

import os
import pytest


class TestMaskSecret:
    """_mask_secret: masks sensitive strings for display."""

    def test_masks_normal_string(self, plugin):
        result = plugin._mask_secret("abcdefgh")
        assert result == "ab****gh"

    def test_masks_short_string(self, plugin):
        assert plugin._mask_secret("abc") == "***"

    def test_masks_4_char_string(self, plugin):
        assert plugin._mask_secret("abcd") == "****"

    def test_masks_5_char_string(self, plugin):
        result = plugin._mask_secret("abcde")
        assert result == "ab*de"

    def test_none_input(self, plugin):
        assert plugin._mask_secret(None) == ""

    def test_empty_string(self, plugin):
        assert plugin._mask_secret("") == ""

    def test_numeric_input(self, plugin):
        result = plugin._mask_secret(12345678)
        assert result == "12****78"


class TestAppendLog:
    """_append_log: write log entry to source log file."""

    def test_creates_log_file(self, plugin):
        plugin._append_log("src1", "test message")
        log_path = os.path.join(plugin.log_dir, "src1.log")
        assert os.path.exists(log_path)
        content = open(log_path, "r", encoding="utf-8").read()
        assert "test message" in content

    def test_appends_multiple_entries(self, plugin):
        plugin._append_log("src1", "msg1")
        plugin._append_log("src1", "msg2")
        log_path = os.path.join(plugin.log_dir, "src1.log")
        content = open(log_path, "r", encoding="utf-8").read()
        assert "msg1" in content
        assert "msg2" in content


class TestAppendTaskLog:
    """_append_task_log: write log entry to task log file."""

    def test_creates_task_log_file(self, plugin):
        plugin._append_task_log("task_001", "step started")
        log_path = os.path.join(plugin.log_dir, "task_task_001.log")
        assert os.path.exists(log_path)
        content = open(log_path, "r", encoding="utf-8").read()
        assert "step started" in content


class TestAudit:
    """_audit: append audit entry to config data."""

    def test_creates_audit_entry(self, plugin):
        data = {"audit_logs": []}
        plugin._audit(data, "test_action", {"key": "value"})
        assert len(data["audit_logs"]) == 1
        entry = data["audit_logs"][0]
        assert entry["action"] == "test_action"
        assert entry["detail"] == {"key": "value"}
        assert "id" in entry
        assert entry["id"].startswith("audit_")

    def test_audit_max_2000(self, plugin):
        data = {"audit_logs": [{"id": "old_{}".format(i)} for i in range(2000)]}
        plugin._audit(data, "new_action", {})
        assert len(data["audit_logs"]) == 2000
        assert data["audit_logs"][0]["action"] == "new_action"

    def test_audit_prepends(self, plugin):
        data = {"audit_logs": [{"id": "old", "action": "old"}]}
        plugin._audit(data, "new", {})
        assert data["audit_logs"][0]["action"] == "new"


class TestCreateSnapshot:
    """_create_snapshot: append change snapshot."""

    def test_creates_snapshot(self, plugin):
        data = {"change_snapshots": []}
        snap = plugin._create_snapshot(data, "config_change", {"before": "a", "after": "b"})
        assert snap["category"] == "config_change"
        assert snap["payload"] == {"before": "a", "after": "b"}
        assert snap["snapshot_id"].startswith("snap_")
        assert len(data["change_snapshots"]) == 1

    def test_snapshot_max_500(self, plugin):
        data = {"change_snapshots": [{"snapshot_id": "old_{}".format(i)} for i in range(500)]}
        plugin._create_snapshot(data, "test", {})
        assert len(data["change_snapshots"]) == 500

    def test_snapshot_prepends(self, plugin):
        data = {"change_snapshots": [{"snapshot_id": "old", "category": "old"}]}
        plugin._create_snapshot(data, "new_cat", {})
        assert data["change_snapshots"][0]["category"] == "new_cat"
