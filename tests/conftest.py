# coding: utf-8
"""Shared fixtures and mock infrastructure for mms/ tests.

The real application inherits from multiple Mixin classes defined in mms/ and
the main plugin class in mysql_multi_source_main.py.  To isolate tests from
the BT-Panel runtime we:

1. Mock the ``public`` module BEFORE any mms/ import touches it.
2. Build a ``FakePlugin`` that composes the same Mixins the real class does,
   but only wires the helper attributes each test needs.
"""

import sys
import types
import time
import json
import os
import contextlib
import tempfile
import threading
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# 1.  Inject a fake ``public`` module into sys.path so that mms/ modules
#     that ``import public`` succeed without the real BT-Panel runtime.
# ---------------------------------------------------------------------------
public_mod = types.ModuleType("public")


def _return_msg(status, msg):
    """Mirror BT-Panel's returnMsg helper."""
    return {"status": status, "msg": msg}


def _read_file(path):
    """Mirror BT-Panel's ReadFile: read file content as string."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _write_file(path, data, mode="w"):
    """Mirror BT-Panel's WriteFile: write data to file."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, mode, encoding="utf-8") as f:
            f.write(data)
        return True
    except Exception:
        return False


public_mod.returnMsg = _return_msg
public_mod.ReadFile = _read_file
public_mod.WriteFile = _write_file
public_mod.writeFile = _write_file
public_mod.ExecShell = MagicMock(return_value=("", ""))


def _to_dict_obj(d):
    """Mirror BT-Panel's to_dict_obj: convert dict to attribute-accessible obj."""
    obj = types.SimpleNamespace(**d)
    return obj


public_mod.to_dict_obj = _to_dict_obj

sys.modules["public"] = public_mod

# Now it's safe to import the mms modules
from mms.validators import ValidatorsMixin
from mms.crypto import CryptoMixin
from mms.config_store import ConfigStoreMixin
from mms.logging_audit import LoggingAuditMixin
from mms.handshake_service import HandshakeServiceMixin
from mms.replication_syntax import ReplicationSyntaxMixin
from mms.dashboard_service import DashboardServiceMixin
from mms.diagnose_service import DiagnoseServiceMixin


# ---------------------------------------------------------------------------
# 2.  FakePlugin: compose all Mixins + wire helpers
# ---------------------------------------------------------------------------

class FakePlugin(
    ValidatorsMixin,
    CryptoMixin,
    ConfigStoreMixin,
    LoggingAuditMixin,
    HandshakeServiceMixin,
    ReplicationSyntaxMixin,
    DashboardServiceMixin,
    DiagnoseServiceMixin,
):
    """A minimal stand-in for the real main plugin class.

    Provides the constants and helpers that the Mixins reference via ``self``.
    """

    CONFIG_SCHEMA_VERSION = "2.0.0"
    CRYPTO_PREFIX = "enc:v1:"

    def __init__(self, tmpdir=None):
        base = tmpdir or tempfile.mkdtemp(prefix="mms_test_")
        self.plugin_root = base
        self.config_path = os.path.join(base, "multi_source_info.json")
        self.config_lock_path = os.path.join(base, "multi_source_info.lock")
        self.log_dir = os.path.join(base, "log")
        self.bootstrap_root = os.path.join(base, "bootstrap_data")
        self.crypto_key_path = os.path.join(base, "secret.key")
        self.sign_secret_path = os.path.join(base, "profile_sign.key")
        self._mysql_version_cache = None
        self._lock = threading.Lock()

    # --- helpers the mixins expect ---

    def _now(self):
        return int(time.time())

    def _find_source(self, data, source_id):
        for item in data.get("sources", []):
            if item.get("source_id") == source_id:
                return item
        return None

    def _ok(self, data=None, message="ok", code="OK"):
        payload = data if data is not None else {}
        if isinstance(payload, dict):
            payload.setdefault("message", message)
            payload.setdefault("code", code)
        return {"status": True, "msg": payload}

    def _fail(self, message, code="ERR_GENERIC", data=None):
        payload = {"message": str(message), "code": code}
        if isinstance(data, dict):
            payload.update(data)
        return {"status": False, "msg": payload}

    def _query_sql(self, sql):
        """Stub: override in tests that need DB interaction."""
        return []

    def _with_lock(self):
        """Provide a real threading lock for concurrency tests."""
        @contextlib.contextmanager
        def _lock_ctx():
            self._lock.acquire()
            try:
                yield
            finally:
                self._lock.release()
        return _lock_ctx()

    # --- config helpers used by some tests ---

    def _load_config(self):
        """Override to use the real ConfigStoreMixin after _ensure_dirs."""
        self._ensure_dirs()
        return ConfigStoreMixin._load_config(self)


# ---------------------------------------------------------------------------
# 3.  Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def plugin(tmp_path):
    """Return a FakePlugin rooted in a fresh temp directory."""
    return FakePlugin(tmpdir=str(tmp_path))


@pytest.fixture
def public_mock():
    """Return the fake public module for direct assertions."""
    return public_mod
