# coding: utf-8
"""Tests for mms/validators.py"""

import pytest


class TestValidateChannelName:
    """_validate_channel_name: alphanumeric + underscore, 1-64 chars."""

    def test_simple_valid(self, plugin):
        assert plugin._validate_channel_name("ch1") is True

    def test_underscore(self, plugin):
        assert plugin._validate_channel_name("my_channel_01") is True

    def test_max_length_64(self, plugin):
        assert plugin._validate_channel_name("a" * 64) is True

    def test_exceeds_64(self, plugin):
        assert plugin._validate_channel_name("a" * 65) is False

    def test_empty(self, plugin):
        assert plugin._validate_channel_name("") is False

    def test_none(self, plugin):
        assert plugin._validate_channel_name(None) is False

    def test_hyphen_rejected(self, plugin):
        assert plugin._validate_channel_name("my-channel") is False

    def test_space_rejected(self, plugin):
        assert plugin._validate_channel_name("my channel") is False

    def test_sql_injection(self, plugin):
        assert plugin._validate_channel_name("ch'; DROP TABLE--") is False

    def test_unicode_rejected(self, plugin):
        assert plugin._validate_channel_name("通道1") is False


class TestValidateSourceId:
    """_validate_source_id: alphanumeric + underscore + hyphen, 1-64 chars."""

    def test_simple_valid(self, plugin):
        assert plugin._validate_source_id("src1") is True

    def test_hyphen_valid(self, plugin):
        assert plugin._validate_source_id("source-id") is True

    def test_underscore_valid(self, plugin):
        assert plugin._validate_source_id("source_id") is True

    def test_mixed(self, plugin):
        assert plugin._validate_source_id("src-01_prod") is True

    def test_max_length(self, plugin):
        assert plugin._validate_source_id("x" * 64) is True

    def test_exceeds_max(self, plugin):
        assert plugin._validate_source_id("x" * 65) is False

    def test_empty(self, plugin):
        assert plugin._validate_source_id("") is False

    def test_none(self, plugin):
        assert plugin._validate_source_id(None) is False

    def test_dot_rejected(self, plugin):
        assert plugin._validate_source_id("src.1") is False

    def test_space_rejected(self, plugin):
        assert plugin._validate_source_id("src 1") is False

    def test_sql_injection(self, plugin):
        assert plugin._validate_source_id("id' OR 1=1--") is False


class TestValidateMysqlScopeName:
    """_validate_mysql_scope_name: alphanumeric + underscore + hyphen + dollar."""

    def test_simple(self, plugin):
        assert plugin._validate_mysql_scope_name("db1") is True

    def test_dollar_sign(self, plugin):
        assert plugin._validate_mysql_scope_name("sys$replica") is True

    def test_hyphen(self, plugin):
        assert plugin._validate_mysql_scope_name("my-db") is True

    def test_empty(self, plugin):
        assert plugin._validate_mysql_scope_name("") is False

    def test_none(self, plugin):
        assert plugin._validate_mysql_scope_name(None) is False

    def test_space_rejected(self, plugin):
        assert plugin._validate_mysql_scope_name("my db") is False

    def test_special_chars(self, plugin):
        assert plugin._validate_mysql_scope_name("db;DROP") is False


class TestValidatePrivilegesText:
    """_validate_privileges_text: alpha + comma + whitespace."""

    def test_single_priv(self, plugin):
        assert plugin._validate_privileges_text("REPLICATION SLAVE") is True

    def test_multiple_privs(self, plugin):
        assert plugin._validate_privileges_text("REPLICATION SLAVE, REPLICATION CLIENT") is True

    def test_comma_separated(self, plugin):
        assert plugin._validate_privileges_text("SELECT,INSERT,UPDATE") is True

    def test_empty(self, plugin):
        assert plugin._validate_privileges_text("") is False

    def test_none(self, plugin):
        assert plugin._validate_privileges_text(None) is False

    def test_digits_rejected(self, plugin):
        assert plugin._validate_privileges_text("PRIV123") is False

    def test_star_rejected(self, plugin):
        assert plugin._validate_privileges_text("ALL PRIVILEGES *.*") is False

    def test_semicolon_injection(self, plugin):
        assert plugin._validate_privileges_text("SELECT; DROP TABLE") is False


class TestValidateGtidSet:
    """_validate_gtid_set: MySQL GTID format UUID:interval,..."""

    # --- valid cases ---
    def test_single_uuid_single_range(self, plugin):
        gtid = "3E11FA47-71CA-11E1-9E33-C80AA9429562:1-5"
        assert plugin._validate_gtid_set(gtid) is True

    def test_single_uuid_multiple_ranges(self, plugin):
        gtid = "3E11FA47-71CA-11E1-9E33-C80AA9429562:1-5:6-10"
        assert plugin._validate_gtid_set(gtid) is True

    def test_multiple_uuids(self, plugin):
        gtid = (
            "3E11FA47-71CA-11E1-9E33-C80AA9429562:1-5,"
            "2C6B1A2F-71CA-11E1-9E33-C80AA9429562:1-3"
        )
        assert plugin._validate_gtid_set(gtid) is True

    def test_single_transaction(self, plugin):
        gtid = "3E11FA47-71CA-11E1-9E33-C80AA9429562:5"
        assert plugin._validate_gtid_set(gtid) is True

    def test_complex_multi_range(self, plugin):
        gtid = (
            "3E11FA47-71CA-11E1-9E33-C80AA9429562:1-5:7-10:15,"
            "2C6B1A2F-71CA-11E1-9E33-C80AA9429562:1-3:5"
        )
        assert plugin._validate_gtid_set(gtid) is True

    # --- invalid cases ---
    def test_sql_injection_single_quote(self, plugin):
        assert plugin._validate_gtid_set("123:456'; DROP TABLE--") is False

    def test_sql_injection_or(self, plugin):
        assert plugin._validate_gtid_set("1:1 OR 1=1") is False

    def test_sql_injection_union(self, plugin):
        assert plugin._validate_gtid_set("1:1 UNION SELECT * FROM users") is False

    def test_empty(self, plugin):
        assert plugin._validate_gtid_set("") is False

    def test_none(self, plugin):
        assert plugin._validate_gtid_set(None) is False

    def test_random_string(self, plugin):
        assert plugin._validate_gtid_set("not-a-gtid") is False

    def test_missing_interval(self, plugin):
        assert plugin._validate_gtid_set("3E11FA47-71CA-11E1-9E33-C80AA9429562:") is False

    def test_uuid_too_short(self, plugin):
        assert plugin._validate_gtid_set("3E11FA47-71CA-11E1-9E33:1-5") is False

    def test_leading_trailing_spaces_stripped(self, plugin):
        gtid = "  3E11FA47-71CA-11E1-9E33-C80AA9429562:1-5  "
        assert plugin._validate_gtid_set(gtid) is True

    def test_comma_only(self, plugin):
        assert plugin._validate_gtid_set(",") is False
