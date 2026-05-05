# coding: utf-8

import os
import json

import public


class ConfigStoreMixin(object):
    def _default_config(self):
        return {
            "version": self.CONFIG_SCHEMA_VERSION,
            "mode": "replica_mode",
            "slave_instance": {
                "host": "127.0.0.1",
                "port": 3306,
                "updated_at": self._now(),
            },
            "master_setup": None,
            "sources": [],
            "bootstrap_tasks": [],
            "master_profiles": [],
            "handshake_sessions": [],
            "change_snapshots": [],
            "audit_logs": [],
        }

    def _ensure_dirs(self):
        plugin_dir = os.path.dirname(self.config_path)
        if not os.path.exists(plugin_dir):
            os.makedirs(plugin_dir)
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)
        if not os.path.exists(self.bootstrap_root):
            os.makedirs(self.bootstrap_root)

    def _migrate_config(self, data):
        changed = False
        if not isinstance(data, dict):
            return data, False
        data.setdefault("version", "1")

        for src in data.get("sources", []) or []:
            pwd = src.get("repl_password")
            if pwd and isinstance(pwd, str) and not pwd.startswith(self.CRYPTO_PREFIX):
                src["repl_password"] = self._crypto_encrypt(pwd)
                changed = True

        for profile in data.get("master_profiles", []) or []:
            payload = profile.get("payload") or {}
            pwd = payload.get("repl_password")
            if pwd and isinstance(pwd, str) and not pwd.startswith(self.CRYPTO_PREFIX):
                payload["repl_password"] = self._crypto_encrypt(pwd)
                profile["payload"] = payload
                # 密码加密后 payload 变更，需同步更新签名，否则导入时验签失败
                try:
                    profile["signature"] = self._profile_sign(payload)
                except Exception:
                    pass
                changed = True

        if str(data.get("version", "1")) != self.CONFIG_SCHEMA_VERSION:
            data["version"] = self.CONFIG_SCHEMA_VERSION
            changed = True

        return data, changed

    def _load_config(self):
        self._ensure_dirs()
        if not os.path.exists(self.config_path):
            data = self._default_config()
            public.WriteFile(self.config_path, json.dumps(data, ensure_ascii=False))
            return data
        raw = public.ReadFile(self.config_path)
        if not raw:
            return self._default_config()
        try:
            data = json.loads(raw)
        except Exception:
            return self._default_config()
        data, changed = self._migrate_config(data)
        if changed:
            try:
                public.WriteFile(self.config_path, json.dumps(data, ensure_ascii=False))
            except Exception:
                pass
        return data

    def _save_config(self, data):
        data.setdefault("mode", "replica_mode")
        data.setdefault("master_profiles", [])
        data.setdefault("handshake_sessions", [])
        data.setdefault("change_snapshots", [])
        data.setdefault("audit_logs", [])
        data.setdefault("version", self.CONFIG_SCHEMA_VERSION)
        data.setdefault("slave_instance", {"host": "127.0.0.1", "port": 3306})
        data["slave_instance"]["updated_at"] = self._now()
        for src in data.get("sources", []) or []:
            pwd = src.get("repl_password")
            if pwd and isinstance(pwd, str) and not pwd.startswith(self.CRYPTO_PREFIX):
                src["repl_password"] = self._crypto_encrypt(pwd)
        return bool(public.WriteFile(self.config_path, json.dumps(data, ensure_ascii=False)))

    def _update_config(self, mutator):
        with self._with_lock():
            data = self._load_config()
            ret = mutator(data)
            self._save_config(data)
            return ret
