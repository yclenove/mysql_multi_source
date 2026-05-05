# coding: utf-8
"""Tests for mms/handshake_service.py"""

import base64
import json
import os
import time
import types

import pytest


def _make_get(**kwargs):
    """Create a SimpleNamespace that mimics BT-Panel's get object."""
    return types.SimpleNamespace(**kwargs)


class TestMasterExportSignedProfile:
    """master_export_signed_profile: export config profile with encrypted password."""

    def test_export_success(self, plugin):
        get = _make_get(
            source_id="s1",
            channel_name="ch1",
            master_host="10.0.0.1",
            master_port=3306,
            repl_user="repl",
            repl_password="secret123",
        )
        result = plugin.master_export_signed_profile(get)
        assert result["status"] is True
        msg = result["msg"]
        assert "profile_id" in msg
        assert "profile_b64" in msg
        assert "signature" in msg

    def test_export_password_encrypted_in_payload(self, plugin):
        get = _make_get(
            source_id="s1",
            channel_name="ch1",
            master_host="10.0.0.1",
            master_port=3306,
            repl_user="repl",
            repl_password="my_secret",
        )
        result = plugin.master_export_signed_profile(get)
        # Decode the base64 profile to inspect payload
        raw = base64.b64decode(result["msg"]["profile_b64"].encode("utf-8")).decode("utf-8")
        obj = json.loads(raw)
        pwd = obj["payload"]["repl_password"]
        assert pwd.startswith(plugin.CRYPTO_PREFIX)
        assert pwd != "my_secret"

    def test_export_missing_param(self, plugin):
        get = _make_get(source_id="s1")  # missing required fields
        result = plugin.master_export_signed_profile(get)
        assert result["status"] is False

    def test_export_with_db_mappings(self, plugin):
        get = _make_get(
            source_id="s1",
            channel_name="ch1",
            master_host="10.0.0.1",
            master_port=3306,
            repl_user="repl",
            repl_password="pwd",
            db_mappings='[{"source_db":"db1","target_db":"db1_copy"}]',
        )
        result = plugin.master_export_signed_profile(get)
        assert result["status"] is True

    def test_export_with_custom_ttl(self, plugin):
        get = _make_get(
            source_id="s1",
            channel_name="ch1",
            master_host="10.0.0.1",
            master_port=3306,
            repl_user="repl",
            repl_password="pwd",
            ttl_seconds="7200",
        )
        result = plugin.master_export_signed_profile(get)
        assert result["status"] is True


class TestMasterGetProfile:
    """master_get_profile: retrieve profile by ID."""

    def test_get_existing_profile(self, plugin):
        # First export
        get = _make_get(
            source_id="s1", channel_name="ch1", master_host="10.0.0.1",
            master_port=3306, repl_user="repl", repl_password="pwd",
        )
        export_result = plugin.master_export_signed_profile(get)
        pid = export_result["msg"]["profile_id"]
        # Then get
        result = plugin.master_get_profile(_make_get(profile_id=pid))
        assert result["status"] is True
        assert result["msg"]["profile_id"] == pid

    def test_get_nonexistent_profile(self, plugin):
        result = plugin.master_get_profile(_make_get(profile_id="profile_nonexist"))
        assert result["status"] is False

    def test_get_missing_param(self, plugin):
        result = plugin.master_get_profile(_make_get())
        assert result["status"] is False


class TestReplicaVerifyProfile:
    """replica_verify_profile: verify signature and expiry."""

    def _export_and_get_b64(self, plugin):
        get = _make_get(
            source_id="s1", channel_name="ch1", master_host="10.0.0.1",
            master_port=3306, repl_user="repl", repl_password="pwd",
        )
        result = plugin.master_export_signed_profile(get)
        return result["msg"]["profile_b64"]

    def test_verify_valid_profile(self, plugin):
        b64 = self._export_and_get_b64(plugin)
        result = plugin.replica_verify_profile(_make_get(profile_b64=b64))
        assert result["status"] is True
        assert result["msg"]["verified"] is True

    def test_verify_missing_param(self, plugin):
        result = plugin.replica_verify_profile(_make_get())
        assert result["status"] is False

    def test_verify_tampered_signature(self, plugin):
        b64 = self._export_and_get_b64(plugin)
        obj = json.loads(base64.b64decode(b64.encode("utf-8")).decode("utf-8"))
        obj["signature"] = "0" * 64  # tamper
        tampered = base64.b64encode(json.dumps(obj).encode("utf-8")).decode("utf-8")
        result = plugin.replica_verify_profile(_make_get(profile_b64=tampered))
        assert result["status"] is False
        assert result["msg"]["code"] == "ERR_PROFILE_SIGNATURE"

    def test_verify_expired_profile(self, plugin, monkeypatch):
        """Create a profile with short TTL, then advance time past expiry."""
        # Use a tiny TTL so we can expire it by advancing _now()
        now_value = int(time.time())
        monkeypatch.setattr(plugin, "_now", lambda: now_value)
        get = _make_get(
            source_id="s1", channel_name="ch1", master_host="10.0.0.1",
            master_port=3306, repl_user="repl", repl_password="pwd",
            ttl_seconds="10",
        )
        result = plugin.master_export_signed_profile(get)
        b64 = result["msg"]["profile_b64"]
        # Advance time past the TTL
        monkeypatch.setattr(plugin, "_now", lambda: now_value + 100)
        verify = plugin.replica_verify_profile(_make_get(profile_b64=b64))
        assert verify["status"] is False
        assert verify["msg"]["code"] == "ERR_PROFILE_EXPIRED"

    def test_verify_missing_fields(self, plugin):
        """Profile with missing required fields should fail."""
        obj = {
            "profile_id": "test",
            "payload": {"source_id": "s1"},  # missing other fields
            "signature": "abc",
        }
        b64 = base64.b64encode(json.dumps(obj).encode("utf-8")).decode("utf-8")
        result = plugin.replica_verify_profile(_make_get(profile_b64=b64))
        assert result["status"] is False
        assert result["msg"]["code"] == "ERR_PROFILE_FIELDS"

    def test_verify_empty_string(self, plugin):
        result = plugin.replica_verify_profile(_make_get(profile_b64=""))
        assert result["status"] is False

    def test_verify_garbage_base64(self, plugin):
        result = plugin.replica_verify_profile(_make_get(profile_b64="not_valid_b64!!!"))
        assert result["status"] is False


class TestReplicaImportProfile:
    """replica_import_profile: import a verified profile as a new source."""

    def _export_b64(self, plugin, password="pwd123"):
        get = _make_get(
            source_id="s1", channel_name="ch1", master_host="10.0.0.1",
            master_port=3306, repl_user="repl", repl_password=password,
        )
        result = plugin.master_export_signed_profile(get)
        return result["msg"]["profile_b64"]

    def test_import_success(self, plugin):
        b64 = self._export_b64(plugin)
        result = plugin.replica_import_profile(_make_get(profile_b64=b64))
        assert result["status"] is True
        assert result["msg"]["source_id"] == "s1"

    def test_import_duplicate_source(self, plugin):
        b64 = self._export_b64(plugin)
        plugin.replica_import_profile(_make_get(profile_b64=b64))
        result = plugin.replica_import_profile(_make_get(profile_b64=b64))
        assert result["status"] is False
        assert result["msg"]["code"] == "ERR_DUPLICATE"

    def test_import_verifies_signature(self, plugin):
        """Tampered profiles should be rejected."""
        b64 = self._export_b64(plugin)
        obj = json.loads(base64.b64decode(b64.encode("utf-8")).decode("utf-8"))
        obj["signature"] = "0" * 64
        tampered = base64.b64encode(json.dumps(obj).encode("utf-8")).decode("utf-8")
        result = plugin.replica_import_profile(_make_get(profile_b64=tampered))
        assert result["status"] is False

    def test_import_decrypts_password(self, plugin):
        """After import, the stored password should be re-encrypted."""
        b64 = self._export_b64(plugin, password="my_real_pwd")
        plugin.replica_import_profile(_make_get(profile_b64=b64))
        data = plugin._load_config()
        source = plugin._find_source(data, "s1")
        assert source is not None
        # Password should be encrypted (not plaintext)
        stored_pwd = source["repl_password"]
        assert stored_pwd.startswith(plugin.CRYPTO_PREFIX)
        assert plugin._crypto_decrypt(stored_pwd) == "my_real_pwd"


class TestHandshakeCreateAndStatus:
    """master_create_handshake / handshake_status / handshake_overview."""

    def _export_b64(self, plugin):
        get = _make_get(
            source_id="s1", channel_name="ch1", master_host="10.0.0.1",
            master_port=3306, repl_user="repl", repl_password="pwd",
        )
        return plugin.master_export_signed_profile(get)["msg"]["profile_b64"]

    def test_create_handshake(self, plugin):
        b64 = self._export_b64(plugin)
        result = plugin.master_create_handshake(_make_get(profile_b64=b64))
        assert result["status"] is True
        assert "token" in result["msg"]
        assert result["msg"]["token"].startswith("hs_")

    def test_handshake_status_pending(self, plugin):
        b64 = self._export_b64(plugin)
        create = plugin.master_create_handshake(_make_get(profile_b64=b64))
        token = create["msg"]["token"]
        status = plugin.handshake_status(_make_get(token=token))
        assert status["status"] is True
        assert status["msg"]["status"] == "pending"

    def test_handshake_status_not_found(self, plugin):
        result = plugin.handshake_status(_make_get(token="hs_nonexistent"))
        assert result["status"] is False
        assert result["msg"]["code"] == "ERR_HANDSHAKE_NOT_FOUND"

    def test_handshake_status_missing_token(self, plugin):
        result = plugin.handshake_status(_make_get())
        assert result["status"] is False

    def test_handshake_overview(self, plugin):
        result = plugin.handshake_overview()
        assert result["status"] is True
        assert "total_sessions" in result["msg"]

    def test_handshake_expired_detection(self, plugin, monkeypatch):
        now_value = int(time.time())
        monkeypatch.setattr(plugin, "_now", lambda: now_value)
        b64 = self._export_b64(plugin)
        create = plugin.master_create_handshake(
            _make_get(profile_b64=b64, ttl_seconds="10")
        )
        token = create["msg"]["token"]
        # Advance time past the TTL
        monkeypatch.setattr(plugin, "_now", lambda: now_value + 100)
        status = plugin.handshake_status(_make_get(token=token))
        assert status["status"] is True
        assert status["msg"]["status"] == "expired"

    def test_overview_counts(self, plugin):
        b64 = self._export_b64(plugin)
        plugin.master_create_handshake(_make_get(profile_b64=b64))
        plugin.master_create_handshake(_make_get(profile_b64=b64))
        overview = plugin.handshake_overview()
        assert overview["msg"]["total_sessions"] == 2


class TestReplicaAcceptHandshake:
    """replica_accept_handshake: consume a handshake token."""

    def _export_and_create_handshake(self, plugin):
        get = _make_get(
            source_id="s1", channel_name="ch1", master_host="10.0.0.1",
            master_port=3306, repl_user="repl", repl_password="pwd",
        )
        b64 = plugin.master_export_signed_profile(get)["msg"]["profile_b64"]
        hs = plugin.master_create_handshake(_make_get(profile_b64=b64))
        return hs["msg"]["token"]

    def test_accept_success(self, plugin):
        token = self._export_and_create_handshake(plugin)
        result = plugin.replica_accept_handshake(_make_get(token=token))
        assert result["status"] is True
        assert result["msg"]["source_id"] == "s1"

    def test_accept_already_consumed(self, plugin):
        token = self._export_and_create_handshake(plugin)
        plugin.replica_accept_handshake(_make_get(token=token))
        result = plugin.replica_accept_handshake(_make_get(token=token))
        assert result["status"] is False
        assert result["msg"]["code"] == "ERR_HANDSHAKE_CONSUMED"

    def test_accept_expired(self, plugin, monkeypatch):
        now_value = int(time.time())
        monkeypatch.setattr(plugin, "_now", lambda: now_value)
        get = _make_get(
            source_id="s1", channel_name="ch1", master_host="10.0.0.1",
            master_port=3306, repl_user="repl", repl_password="pwd",
        )
        b64 = plugin.master_export_signed_profile(get)["msg"]["profile_b64"]
        hs = plugin.master_create_handshake(
            _make_get(profile_b64=b64, ttl_seconds="10")
        )
        token = hs["msg"]["token"]
        # Advance time past the TTL
        monkeypatch.setattr(plugin, "_now", lambda: now_value + 100)
        result = plugin.replica_accept_handshake(_make_get(token=token))
        assert result["status"] is False
        assert result["msg"]["code"] == "ERR_HANDSHAKE_EXPIRED"

    def test_accept_not_found(self, plugin):
        result = plugin.replica_accept_handshake(_make_get(token="hs_nope"))
        assert result["status"] is False
        assert result["msg"]["code"] == "ERR_HANDSHAKE_NOT_FOUND"

    def test_accept_missing_token(self, plugin):
        result = plugin.replica_accept_handshake(_make_get())
        assert result["status"] is False


class TestHandshakeStatusPayload:
    """_handshake_status_payload: internal helper for status formatting."""

    def test_pending_session(self, plugin):
        session = {
            "token": "hs_abc",
            "profile_id": "p1",
            "source_id": "s1",
            "channel_name": "ch1",
            "status": "pending",
            "consumed": False,
            "created_at": plugin._now(),
            "expires_at": plugin._now() + 3600,
            "accept_attempts": 0,
            "last_error": "",
            "last_error_code": "",
            "accepted_at": 0,
        }
        payload = plugin._handshake_status_payload(session)
        assert payload["status"] == "pending"
        assert payload["expired"] is False
        assert payload["code"] == "HANDSHAKE_PENDING"

    def test_consumed_session(self, plugin):
        session = {
            "status": "consumed",
            "consumed": True,
            "created_at": plugin._now(),
            "expires_at": plugin._now() + 3600,
            "accept_attempts": 1,
            "last_error": "",
            "last_error_code": "",
            "accepted_at": plugin._now(),
        }
        payload = plugin._handshake_status_payload(session)
        assert payload["status"] == "consumed"
        assert payload["code"] == "HANDSHAKE_CONSUMED"

    def test_failed_session(self, plugin):
        session = {
            "status": "failed",
            "consumed": True,
            "created_at": plugin._now(),
            "expires_at": plugin._now() + 3600,
            "accept_attempts": 3,
            "last_error": "some error",
            "last_error_code": "ERR_SOMETHING",
            "accepted_at": 0,
        }
        payload = plugin._handshake_status_payload(session)
        assert payload["status"] == "failed"
        assert payload["code"] == "ERR_SOMETHING"

    def test_non_dict_input(self, plugin):
        assert plugin._handshake_status_payload(None) == {}
        assert plugin._handshake_status_payload("string") == {}


class TestHandshakeOverviewPayload:
    """_handshake_overview_payload: aggregation helper."""

    def test_empty_sessions(self, plugin):
        result = plugin._handshake_overview_payload([])
        assert result["total_sessions"] == 0

    def test_counts_by_status(self, plugin):
        now = plugin._now()
        sessions = [
            {"status": "pending", "created_at": now, "expires_at": now + 3600, "accept_attempts": 0,
             "last_error": "", "last_error_code": "", "accepted_at": 0},
            {"status": "consumed", "created_at": now, "expires_at": now + 3600, "accept_attempts": 1,
             "last_error": "", "last_error_code": "", "accepted_at": now},
            {"status": "failed", "created_at": now, "expires_at": now + 3600, "accept_attempts": 3,
             "last_error": "err", "last_error_code": "ERR_X", "accepted_at": 0,
             "last_error_at": now},
        ]
        result = plugin._handshake_overview_payload(sessions)
        assert result["total_sessions"] == 3
        assert result["status_counts"]["pending"] == 1
        assert result["status_counts"]["consumed"] == 1
        assert result["status_counts"]["failed"] == 1

    def test_failure_code_rows(self, plugin):
        now = plugin._now()
        sessions = [
            {"status": "failed", "created_at": now, "expires_at": now + 3600, "accept_attempts": 2,
             "last_error": "e1", "last_error_code": "ERR_A", "accepted_at": 0, "last_error_at": now},
            {"status": "failed", "created_at": now, "expires_at": now + 3600, "accept_attempts": 2,
             "last_error": "e2", "last_error_code": "ERR_A", "accepted_at": 0, "last_error_at": now},
        ]
        result = plugin._handshake_overview_payload(sessions)
        codes = {r["code"]: r["count"] for r in result["failure_code_rows"]}
        assert codes["ERR_A"] == 2
