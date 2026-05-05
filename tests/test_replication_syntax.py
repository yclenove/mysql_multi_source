# coding: utf-8
"""Tests for mms/replication_syntax.py"""

import pytest
from mms.replication_syntax import (
    mysql_version_tuple,
    is_new_syntax,
    replication_sql,
    ReplicationSyntaxMixin,
)


class TestMysqlVersionTuple:
    """mysql_version_tuple: parse version string to (major, minor, patch)."""

    def test_standard_version(self):
        assert mysql_version_tuple("8.0.23") == (8, 0, 23)

    def test_version_with_suffix(self):
        assert mysql_version_tuple("5.7.35-log") == (5, 7, 35)

    def test_version_with_suffix_2(self):
        assert mysql_version_tuple("8.0.23-commercial") == (8, 0, 23)

    def test_mysql_84(self):
        assert mysql_version_tuple("8.4.0") == (8, 4, 0)

    def test_mysql_56(self):
        assert mysql_version_tuple("5.6.51") == (5, 6, 51)

    def test_empty_string(self):
        assert mysql_version_tuple("") == (0, 0, 0)

    def test_none(self):
        assert mysql_version_tuple(None) == (0, 0, 0)

    def test_invalid_format(self):
        assert mysql_version_tuple("not-a-version") == (0, 0, 0)

    def test_only_major(self):
        assert mysql_version_tuple("8") == (0, 0, 0)

    def test_two_parts(self):
        assert mysql_version_tuple("8.0") == (0, 0, 0)

    def test_large_patch(self):
        assert mysql_version_tuple("8.0.999") == (8, 0, 999)


class TestIsNewSyntax:
    """is_new_syntax: version >= (8, 0, 23) => True."""

    def test_exactly_8023(self):
        assert is_new_syntax((8, 0, 23)) is True

    def test_above_8023(self):
        assert is_new_syntax((8, 0, 24)) is True

    def test_mysql_84(self):
        assert is_new_syntax((8, 4, 0)) is True

    def test_mysql_90(self):
        assert is_new_syntax((9, 0, 0)) is True

    def test_below_8023(self):
        assert is_new_syntax((8, 0, 22)) is False

    def test_mysql_57(self):
        assert is_new_syntax((5, 7, 35)) is False

    def test_mysql_56(self):
        assert is_new_syntax((5, 6, 51)) is False

    def test_zero_version(self):
        assert is_new_syntax((0, 0, 0)) is False

    def test_8013(self):
        assert is_new_syntax((8, 0, 13)) is False


class TestReplicationSql:
    """replication_sql: generate correct SQL for each command and version."""

    V_OLD = (5, 7, 35)
    V_NEW = (8, 0, 23)
    V_84 = (8, 4, 0)

    # --- CHANGE_MASTER ---
    def test_change_master_old_syntax(self):
        sql = replication_sql("CHANGE_MASTER", self.V_OLD,
                              channel="ch1", host="10.0.0.1", port=3306,
                              user="repl", pwd="secret")
        assert "CHANGE MASTER TO" in sql
        assert "MASTER_HOST='10.0.0.1'" in sql
        assert "MASTER_PORT=3306" in sql
        assert "MASTER_USER='repl'" in sql
        assert "MASTER_PASSWORD='secret'" in sql
        assert "FOR CHANNEL 'ch1'" in sql

    def test_change_master_new_syntax(self):
        sql = replication_sql("CHANGE_MASTER", self.V_NEW,
                              channel="ch1", host="10.0.0.1", port=3306,
                              user="repl", pwd="secret")
        assert "CHANGE REPLICATION SOURCE TO" in sql
        assert "SOURCE_HOST='10.0.0.1'" in sql
        assert "SOURCE_PORT=3306" in sql
        assert "SOURCE_USER='repl'" in sql
        assert "SOURCE_PASSWORD='secret'" in sql
        assert "FOR CHANNEL 'ch1'" in sql

    def test_change_master_84(self):
        sql = replication_sql("CHANGE_MASTER", self.V_84,
                              channel="ch1", host="10.0.0.1", port=3306,
                              user="repl", pwd="secret")
        assert "CHANGE REPLICATION SOURCE TO" in sql

    # --- START ---
    def test_start_old(self):
        sql = replication_sql("START", self.V_OLD, channel="ch1")
        assert sql == "START SLAVE FOR CHANNEL 'ch1'"

    def test_start_new(self):
        sql = replication_sql("START", self.V_NEW, channel="ch1")
        assert sql == "START REPLICA FOR CHANNEL 'ch1'"

    # --- STOP ---
    def test_stop_old(self):
        sql = replication_sql("STOP", self.V_OLD, channel="ch1")
        assert sql == "STOP SLAVE FOR CHANNEL 'ch1'"

    def test_stop_new(self):
        sql = replication_sql("STOP", self.V_NEW, channel="ch1")
        assert sql == "STOP REPLICA FOR CHANNEL 'ch1'"

    # --- SHOW_STATUS ---
    def test_show_status_old(self):
        sql = replication_sql("SHOW_STATUS", self.V_OLD, channel="ch1")
        assert sql == "SHOW SLAVE STATUS FOR CHANNEL 'ch1'"

    def test_show_status_new(self):
        sql = replication_sql("SHOW_STATUS", self.V_NEW, channel="ch1")
        assert sql == "SHOW REPLICA STATUS FOR CHANNEL 'ch1'"

    # --- RESET ---
    def test_reset_old(self):
        sql = replication_sql("RESET", self.V_OLD, channel="ch1")
        assert sql == "RESET SLAVE FOR CHANNEL 'ch1'"

    def test_reset_new(self):
        sql = replication_sql("RESET", self.V_NEW, channel="ch1")
        assert sql == "RESET REPLICA FOR CHANNEL 'ch1'"

    # --- Error cases ---
    def test_unknown_command_raises(self):
        with pytest.raises(ValueError, match="未知的复制命令类型"):
            replication_sql("INVALID_CMD", self.V_OLD, channel="ch1")


class TestReplicationSyntaxMixin:
    """ReplicationSyntaxMixin: instance-level version caching and convenience methods."""

    def _make_instance(self, version_str="8.0.23"):
        """Create a minimal instance with the mixin."""
        class FakeInstance(ReplicationSyntaxMixin):
            def _query_sql(self, sql):
                return [{"v": version_str}]
        return FakeInstance()

    def test_get_mysql_version_caches(self):
        inst = self._make_instance("8.0.23")
        v1 = inst._get_mysql_version()
        v2 = inst._get_mysql_version()
        assert v1 == (8, 0, 23)
        assert v1 is v2  # same object due to caching

    def test_get_mysql_version_57(self):
        inst = self._make_instance("5.7.35-log")
        assert inst._get_mysql_version() == (5, 7, 35)

    def test_get_mysql_version_84(self):
        inst = self._make_instance("8.4.0")
        assert inst._get_mysql_version() == (8, 4, 0)

    def test_get_mysql_version_query_failure(self):
        class FailInstance(ReplicationSyntaxMixin):
            def _query_sql(self, sql):
                raise Exception("connection failed")
        inst = FailInstance()
        assert inst._get_mysql_version() == (0, 0, 0)

    def test_get_mysql_version_empty_result(self):
        class EmptyInstance(ReplicationSyntaxMixin):
            def _query_sql(self, sql):
                return []
        inst = EmptyInstance()
        assert inst._get_mysql_version() == (0, 0, 0)

    def test_replication_sql_convenience_method(self):
        class Inst(ReplicationSyntaxMixin):
            def _query_sql(self, sql):
                return [{"v": "8.0.23"}]
        inst = Inst()
        sql = inst._replication_sql("START", channel="ch1")
        assert sql == "START REPLICA FOR CHANNEL 'ch1'"

    def test_replication_sql_old_version(self):
        class Inst(ReplicationSyntaxMixin):
            def _query_sql(self, sql):
                return [{"v": "5.7.35"}]
        inst = Inst()
        sql = inst._replication_sql("STOP", channel="ch1")
        assert sql == "STOP SLAVE FOR CHANNEL 'ch1'"

    def test_replication_sql_change_master_convenience(self):
        class Inst(ReplicationSyntaxMixin):
            def _query_sql(self, sql):
                return [{"v": "8.0.23"}]
        inst = Inst()
        sql = inst._replication_sql(
            "CHANGE_MASTER", channel="ch1",
            host="10.0.0.1", port=3306, user="repl", pwd="pwd"
        )
        assert "CHANGE REPLICATION SOURCE TO" in sql

    def test_tuple_result_from_query(self):
        """_query_sql may return tuples instead of dicts."""
        class TupleInst(ReplicationSyntaxMixin):
            def _query_sql(self, sql):
                return [("8.0.23",)]
        inst = TupleInst()
        assert inst._get_mysql_version() == (8, 0, 23)
