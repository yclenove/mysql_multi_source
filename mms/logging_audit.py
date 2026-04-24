# coding: utf-8

import os
import time
import uuid

import public


class LoggingAuditMixin(object):
    def _append_log(self, source_id, message):
        self._ensure_dirs()
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        log_path = os.path.join(self.log_dir, "{}.log".format(source_id))
        public.writeFile(log_path, "[{}] {}\n".format(ts, message), "a+")

    def _append_task_log(self, task_id, message):
        self._ensure_dirs()
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        log_path = os.path.join(self.log_dir, "task_{}.log".format(task_id))
        public.writeFile(log_path, "[{}] {}\n".format(ts, message), "a+")

    def _mask_secret(self, secret):
        if secret is None:
            return ""
        secret = str(secret)
        if len(secret) <= 4:
            return "*" * len(secret)
        return secret[:2] + "*" * (len(secret) - 4) + secret[-2:]

    def _audit(self, data, action, detail):
        logs = data.setdefault("audit_logs", [])
        logs.insert(0, {
            "id": "audit_" + uuid.uuid4().hex[:10],
            "action": action,
            "detail": detail,
            "created_at": self._now(),
        })
        data["audit_logs"] = logs[:2000]

    def _create_snapshot(self, data, category, payload):
        snaps = data.setdefault("change_snapshots", [])
        snap = {
            "snapshot_id": "snap_" + uuid.uuid4().hex[:12],
            "category": category,
            "payload": payload,
            "created_at": self._now(),
        }
        snaps.insert(0, snap)
        data["change_snapshots"] = snaps[:500]
        return snap
