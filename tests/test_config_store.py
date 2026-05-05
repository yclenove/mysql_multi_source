# coding: utf-8
"""Tests for mms/config_store.py"""

import json
import os
import threading

import pytest


class TestDefaultConfig:
    """_default_config: returns a valid skeleton with expected keys."""

    def test_contains_required_keys(self, plugin):
        cfg = plugin._default_config()
        for key in (
            "version", "mode", "slave_instance", "sources",
            "bootstrap_tasks", "master_profiles", "handshake_sessions",
            "change_snapshots", "audit_logs",
        ):
            assert key in cfg, f"Missing key: {key}"

    def test_version_matches_schema(self, plugin):
        cfg = plugin._default_config()
        assert cfg["version"] == plugin.CONFIG_SCHEMA_VERSION

    def test_mode_is_replica(self, plugin):
        cfg = plugin._default_config()
        assert cfg["mode"] == "replica_mode"

    def test_sources_is_empty_list(self, plugin):
        cfg = plugin._default_config()
        assert cfg["sources"] == []

    def test_slave_instance_defaults(self, plugin):
        cfg = plugin._default_config()
        assert cfg["slave_instance"]["host"] == "127.0.0.1"
        assert cfg["slave_instance"]["port"] == 3306


class TestEnsureDirs:
    """_ensure_dirs: creates plugin_root, log_dir, bootstrap_root."""

    def test_creates_directories(self, plugin, tmp_path):
        # Remove dirs if they exist
        for d in [plugin.plugin_root, plugin.log_dir, plugin.bootstrap_root]:
            if os.path.exists(d):
                os.rmdir(d)
        plugin._ensure_dirs()
        assert os.path.isdir(plugin.plugin_root)
        assert os.path.isdir(plugin.log_dir)
        assert os.path.isdir(plugin.bootstrap_root)

    def test_idempotent(self, plugin):
        plugin._ensure_dirs()
        plugin._ensure_dirs()  # should not raise
        assert os.path.isdir(plugin.plugin_root)


class TestMigrateConfig:
    """_migrate_config: encrypts plaintext passwords, updates version."""

    def test_encrypts_plaintext_source_password(self, plugin):
        data = {
            "version": "1",
            "sources": [
                {"source_id": "s1", "repl_password": "plaintext123"},
            ],
            "master_profiles": [],
        }
        migrated, changed = plugin._migrate_config(data)
        assert changed is True
        pwd = migrated["sources"][0]["repl_password"]
        assert pwd.startswith(plugin.CRYPTO_PREFIX)
        # Should be decryptable
        assert plugin._crypto_decrypt(pwd) == "plaintext123"

    def test_encrypts_plaintext_profile_password(self, plugin):
        data = {
            "version": "1",
            "sources": [],
            "master_profiles": [
                {
                    "payload": {"repl_password": "profile_pwd"},
                    "signature": "old_sig",
                },
            ],
        }
        migrated, changed = plugin._migrate_config(data)
        assert changed is True
        pwd = migrated["master_profiles"][0]["payload"]["repl_password"]
        assert pwd.startswith(plugin.CRYPTO_PREFIX)
        assert plugin._crypto_decrypt(pwd) == "profile_pwd"
        # Signature should be updated
        assert migrated["master_profiles"][0]["signature"] != "old_sig"

    def test_already_encrypted_password_not_re_encrypted(self, plugin):
        already = plugin._crypto_encrypt("secret")
        data = {
            "version": "2.0.0",
            "sources": [{"source_id": "s1", "repl_password": already}],
            "master_profiles": [],
        }
        migrated, changed = plugin._migrate_config(data)
        assert migrated["sources"][0]["repl_password"] == already

    def test_version_upgrade(self, plugin):
        data = {
            "version": "1",
            "sources": [],
            "master_profiles": [],
        }
        migrated, changed = plugin._migrate_config(data)
        assert changed is True
        assert migrated["version"] == plugin.CONFIG_SCHEMA_VERSION

    def test_no_change_when_up_to_date(self, plugin):
        data = {
            "version": plugin.CONFIG_SCHEMA_VERSION,
            "sources": [],
            "master_profiles": [],
        }
        migrated, changed = plugin._migrate_config(data)
        assert changed is False

    def test_non_dict_input(self, plugin):
        result, changed = plugin._migrate_config("not a dict")
        assert changed is False

    def test_empty_sources_and_profiles(self, plugin):
        data = {"version": plugin.CONFIG_SCHEMA_VERSION}
        migrated, changed = plugin._migrate_config(data)
        assert changed is False


class TestLoadConfig:
    """_load_config: create default if missing, migrate if needed."""

    def test_creates_default_when_no_file(self, plugin):
        cfg = plugin._load_config()
        assert cfg["version"] == plugin.CONFIG_SCHEMA_VERSION
        assert cfg["mode"] == "replica_mode"
        assert os.path.exists(plugin.config_path)

    def test_loads_existing_config(self, plugin):
        # First load creates the file
        plugin._load_config()
        # Modify the file
        custom = {"version": plugin.CONFIG_SCHEMA_VERSION, "mode": "custom", "sources": []}
        with open(plugin.config_path, "w", encoding="utf-8") as f:
            json.dump(custom, f)
        cfg = plugin._load_config()
        assert cfg["mode"] == "custom"

    def test_corrupt_json_returns_default(self, plugin):
        os.makedirs(plugin.plugin_root, exist_ok=True)
        with open(plugin.config_path, "w", encoding="utf-8") as f:
            f.write("not json at all {{{")
        cfg = plugin._load_config()
        assert cfg["version"] == plugin.CONFIG_SCHEMA_VERSION

    def test_empty_file_returns_default(self, plugin):
        os.makedirs(plugin.plugin_root, exist_ok=True)
        with open(plugin.config_path, "w", encoding="utf-8") as f:
            f.write("")
        cfg = plugin._load_config()
        assert cfg["version"] == plugin.CONFIG_SCHEMA_VERSION


class TestSaveConfig:
    """_save_config: serializes to JSON, encrypts plaintext passwords."""

    def test_save_creates_file(self, plugin):
        data = plugin._default_config()
        result = plugin._save_config(data)
        assert result is True or result is not None
        assert os.path.exists(plugin.config_path)

    def test_save_encrypts_plaintext_passwords(self, plugin):
        data = plugin._default_config()
        data["sources"] = [{"source_id": "s1", "repl_password": "plain"}]
        plugin._save_config(data)
        with open(plugin.config_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["sources"][0]["repl_password"].startswith(plugin.CRYPTO_PREFIX)

    def test_save_adds_defaults(self, plugin):
        """_save_config should set default keys if missing."""
        data = {"sources": []}
        plugin._save_config(data)
        with open(plugin.config_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["mode"] == "replica_mode"
        assert saved["version"] == plugin.CONFIG_SCHEMA_VERSION


class TestUpdateConfig:
    """_update_config: atomic read-modify-write under lock."""

    def test_mutator_receives_data(self, plugin):
        def mutator(data):
            data["custom_key"] = "injected"
            return "ret_val"
        result = plugin._update_config(mutator)
        assert result == "ret_val"
        cfg = plugin._load_config()
        assert cfg["custom_key"] == "injected"

    def test_concurrent_updates(self, plugin):
        """Multiple threads should not corrupt the config."""
        # Pre-create directories and initial config to avoid Windows race
        # in os.makedirs during concurrent _ensure_dirs calls.
        plugin._ensure_dirs()
        plugin._save_config(plugin._default_config())

        errors = []

        def writer(n):
            try:
                for i in range(5):
                    def mutator(data, _n=n, _i=i):
                        data.setdefault("thread_log", []).append(f"{_n}-{_i}")
                    plugin._update_config(mutator)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        cfg = plugin._load_config()
        assert len(cfg.get("thread_log", [])) == 20
