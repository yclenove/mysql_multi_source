# coding: utf-8

import json
import uuid
import base64

import public


class HandshakeServiceMixin(object):
    def master_export_signed_profile(self, get):
        required = ["source_id", "channel_name", "master_host", "master_port", "repl_user", "repl_password"]
        for k in required:
            if not hasattr(get, k):
                return public.returnMsg(False, "缺少参数: {}".format(k))
        ttl = int(get.ttl_seconds) if hasattr(get, "ttl_seconds") and str(get.ttl_seconds).strip().isdigit() else 3600
        payload = {
            "source_id": str(get.source_id).strip(),
            "channel_name": str(get.channel_name).strip(),
            "master_host": str(get.master_host).strip(),
            "master_port": int(get.master_port),
            "repl_user": str(get.repl_user).strip(),
            "repl_password": str(get.repl_password).strip(),
            "db_mappings": json.loads(get.db_mappings) if hasattr(get, "db_mappings") and str(get.db_mappings).strip() else [],
            "created_at": self._now(),
            "expires_at": self._now() + ttl,
        }
        signature = self._profile_sign(payload)
        data = self._load_config()
        profile_id = "profile_" + uuid.uuid4().hex[:12]
        wrapped = {"profile_id": profile_id, "payload": payload, "signature": signature}
        data.setdefault("master_profiles", []).insert(0, wrapped)
        self._audit(data, "master_export_signed_profile", {"profile_id": profile_id, "source_id": payload["source_id"]})
        self._save_config(data)
        encoded = base64.b64encode(json.dumps(wrapped, ensure_ascii=False).encode("utf-8")).decode("utf-8")
        return public.returnMsg(True, {"profile_id": profile_id, "profile_b64": encoded, "signature": signature})

    def master_get_profile(self, get):
        if not hasattr(get, "profile_id"):
            return public.returnMsg(False, "缺少参数: profile_id")
        data = self._load_config()
        pid = str(get.profile_id).strip()
        for p in data.get("master_profiles", []):
            if p.get("profile_id") == pid:
                return public.returnMsg(True, p)
        return public.returnMsg(False, "未找到该配置单")

    def replica_verify_profile(self, get):
        if not hasattr(get, "profile_b64"):
            return self._fail("缺少参数: profile_b64", "ERR_PARAM_REQUIRED")
        try:
            raw = base64.b64decode(str(get.profile_b64).encode("utf-8")).decode("utf-8")
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                return self._fail("配置单内容不是合法对象", "ERR_PROFILE_FORMAT")
            payload = obj.get("payload", {})
            signature = str(obj.get("signature", "")).strip()
            if not isinstance(payload, dict):
                return self._fail("配置单 payload 格式不正确", "ERR_PROFILE_FORMAT")
            required_keys = ["source_id", "channel_name", "master_host", "master_port", "repl_user", "repl_password"]
            missing = [k for k in required_keys if not payload.get(k)]
            if missing:
                return self._fail("配置单缺少必要字段: {}".format(", ".join(missing)), "ERR_PROFILE_FIELDS")
            if int(payload.get("expires_at", 0)) < self._now():
                return self._fail("配置单已过期，请在主库重新导出", "ERR_PROFILE_EXPIRED")
            if not signature:
                return self._fail("配置单缺少签名，请重新从主库导出", "ERR_PROFILE_SIGNATURE_MISSING")
            if not self._profile_verify(payload, signature):
                return self._fail("配置单签名校验失败，请确认配置单未被篡改并重新导出", "ERR_PROFILE_SIGNATURE")
            return self._ok({"verified": True, "payload": payload}, "配置单签名校验通过", "PROFILE_VERIFIED")
        except (ValueError, TypeError) as ex:
            return self._fail("配置单格式错误，请确认粘贴完整: {}".format(ex), "ERR_PROFILE_FORMAT")
        except Exception as ex:
            return self._fail("配置单解析失败: {}".format(ex), "ERR_PROFILE_PARSE")

    def replica_import_profile(self, get):
        verify = self.replica_verify_profile(get)
        if verify.get("status") is False:
            return verify
        msg = verify.get("msg", {}) if isinstance(verify.get("msg"), dict) else {}
        if not msg.get("verified"):
            return self._fail("配置单签名校验失败，请重新导出", "ERR_PROFILE_SIGNATURE")
        payload = msg.get("payload", {})
        data = self._load_config()
        sid = payload.get("source_id")
        if self._find_source(data, sid):
            return self._fail("该数据源ID已存在", "ERR_DUPLICATE")
        raw_pwd = payload.get("repl_password", "")
        if isinstance(raw_pwd, str) and raw_pwd.startswith(self.CRYPTO_PREFIX):
            raw_pwd = self._crypto_decrypt(raw_pwd)
        source = {
            "source_id": sid,
            "channel_name": payload.get("channel_name"),
            "master_host": payload.get("master_host"),
            "master_port": int(payload.get("master_port", 3306)),
            "repl_user": payload.get("repl_user"),
            "repl_password": self._crypto_encrypt(raw_pwd or ""),
            "sync_mode": "gtid",
            "db_mappings": payload.get("db_mappings", []),
            "init_strategy": "auto",
            "status": {"running": False, "io_running": "No", "sql_running": "No", "seconds_behind": None, "last_error": ""},
            "created_at": self._now(),
            "updated_at": self._now(),
        }
        data["sources"].append(source)
        self._audit(data, "replica_import_profile", {"source_id": sid})
        self._save_config(data)
        return self._ok({"source_id": sid}, "profile导入成功", "PROFILE_IMPORTED")

    def master_create_handshake(self, get):
        if not hasattr(get, "profile_b64"):
            return self._fail("缺少参数: profile_b64", "ERR_PARAM_REQUIRED")
        token = "hs_" + uuid.uuid4().hex
        ttl = int(get.ttl_seconds) if hasattr(get, "ttl_seconds") and str(get.ttl_seconds).strip().isdigit() else 600
        data = self._load_config()
        profile_id = ""
        source_id = ""
        channel_name = ""
        try:
            raw = base64.b64decode(str(get.profile_b64)).decode("utf-8")
            wrapped = json.loads(raw)
            if isinstance(wrapped, dict):
                payload = wrapped.get("payload", {})
                profile_id = str(wrapped.get("profile_id", "") or "")
                if isinstance(payload, dict):
                    source_id = str(payload.get("source_id", "") or "")
                    channel_name = str(payload.get("channel_name", "") or "")
        except Exception:
            pass
        session = {
            "token": token,
            "profile_b64": str(get.profile_b64),
            "profile_id": profile_id,
            "source_id": source_id,
            "channel_name": channel_name,
            "status": "pending",
            "created_at": self._now(),
            "expires_at": self._now() + ttl,
            "consumed": False,
            "accept_attempts": 0,
            "last_error": "",
            "last_error_code": "",
            "last_error_at": 0,
            "accepted_at": 0,
        }
        data.setdefault("handshake_sessions", []).insert(0, session)
        self._audit(data, "master_create_handshake", {"token": token, "expires_at": session["expires_at"]})
        self._save_config(data)
        return self._ok({
            "token": token,
            "expires_at": session["expires_at"],
            "profile_id": profile_id,
            "source_id": source_id,
            "channel_name": channel_name,
        }, "握手令牌已创建", "HANDSHAKE_CREATED")

    def _handshake_status_payload(self, session):
        if not isinstance(session, dict):
            return {}
        now = self._now()
        expires_at = int(session.get("expires_at", 0) or 0)
        expired = expires_at > 0 and expires_at < now
        raw_status = str(session.get("status", "pending") or "pending")
        status = raw_status
        if raw_status == "pending" and expired:
            status = "expired"
        message = "等待从库接收"
        code = "HANDSHAKE_PENDING"
        if status == "consumed":
            message = "握手已被接收"
            code = "HANDSHAKE_CONSUMED"
        elif status == "failed":
            message = str(session.get("last_error", "") or "握手接收失败")
            code = str(session.get("last_error_code", "") or "ERR_HANDSHAKE_ACCEPT")
        elif status == "expired":
            message = str(session.get("last_error", "") or "握手已过期，请在主库重新创建")
            code = str(session.get("last_error_code", "") or "ERR_HANDSHAKE_EXPIRED")
        return {
            "token": session.get("token", ""),
            "profile_id": session.get("profile_id", ""),
            "source_id": session.get("source_id", ""),
            "channel_name": session.get("channel_name", ""),
            "status": status,
            "consumed": bool(session.get("consumed", False)),
            "expired": expired,
            "created_at": session.get("created_at", 0),
            "expires_at": expires_at,
            "accepted_at": int(session.get("accepted_at", 0) or 0),
            "accept_attempts": int(session.get("accept_attempts", 0) or 0),
            "last_error": str(session.get("last_error", "") or ""),
            "last_error_code": str(session.get("last_error_code", "") or ""),
            "message": message,
            "code": code,
        }

    def _handshake_overview_payload(self, sessions):
        rows = []
        status_counts = {"pending": 0, "consumed": 0, "failed": 0, "expired": 0}
        failure_by_code = {}
        total_attempts = 0
        for session in sessions or []:
            row = self._handshake_status_payload(session)
            if not row:
                continue
            rows.append(row)
            status = str(row.get("status", "pending") or "pending")
            status_counts[status] = int(status_counts.get(status, 0) or 0) + 1
            attempts = int(row.get("accept_attempts", 0) or 0)
            total_attempts += attempts
            if status in ("failed", "expired"):
                code = str(row.get("last_error_code", "") or row.get("code", "") or "ERR_HANDSHAKE_ACCEPT")
                failure_by_code[code] = int(failure_by_code.get(code, 0) or 0) + 1

        failure_code_rows = []
        for code, count in sorted(failure_by_code.items(), key=lambda item: (-item[1], item[0])):
            failure_code_rows.append({"code": code, "count": count})

        recent_failed = []
        for row in sorted(rows, key=lambda item: int(item.get("last_error_at", 0) or item.get("created_at", 0) or 0), reverse=True):
            if row.get("status") not in ("failed", "expired"):
                continue
            recent_failed.append({
                "token": row.get("token", ""),
                "profile_id": row.get("profile_id", ""),
                "source_id": row.get("source_id", ""),
                "channel_name": row.get("channel_name", ""),
                "status": row.get("status", ""),
                "last_error": row.get("last_error", ""),
                "last_error_code": row.get("last_error_code", ""),
                "last_error_at": row.get("last_error_at", 0),
                "accept_attempts": row.get("accept_attempts", 0),
            })
            if len(recent_failed) >= 10:
                break

        recent_sessions = sorted(rows, key=lambda item: int(item.get("created_at", 0) or 0), reverse=True)[:10]
        return {
            "total_sessions": len(rows),
            "total_attempts": total_attempts,
            "status_counts": status_counts,
            "failed_sessions": int(status_counts.get("failed", 0) or 0),
            "expired_sessions": int(status_counts.get("expired", 0) or 0),
            "failure_code_rows": failure_code_rows,
            "recent_failed": recent_failed,
            "recent_sessions": recent_sessions,
        }

    def replica_accept_handshake(self, get):
        if not hasattr(get, "token"):
            return self._fail("缺少参数: token", "ERR_PARAM_REQUIRED")
        token = str(get.token).strip()
        data = self._load_config()
        target = None
        for s in data.get("handshake_sessions", []):
            if s.get("token") == token:
                target = s
                break
        if not target:
            return self._fail("未找到该握手令牌", "ERR_HANDSHAKE_NOT_FOUND")
        if target.get("consumed"):
            return self._fail("握手已被消费，请重新创建", "ERR_HANDSHAKE_CONSUMED")
        target["accept_attempts"] = int(target.get("accept_attempts", 0) or 0) + 1
        if int(target.get("expires_at", 0)) < self._now():
            target["status"] = "expired"
            target["last_error"] = "握手已过期，请在主库重新创建"
            target["last_error_code"] = "ERR_HANDSHAKE_EXPIRED"
            target["last_error_at"] = self._now()
            self._audit(data, "replica_accept_handshake", {
                "token": token,
                "status": "expired",
                "accept_attempts": target.get("accept_attempts", 0),
                "last_error": target.get("last_error", ""),
                "last_error_code": target.get("last_error_code", ""),
            })
            self._save_config(data)
            return self._fail(target["last_error"], target["last_error_code"])
        resp = self.replica_import_profile(public.to_dict_obj({"profile_b64": target.get("profile_b64")}))
        target["consumed"] = True
        target["status"] = "consumed" if resp.get("status") else "failed"
        target["accepted_at"] = self._now() if resp.get("status") else 0
        if resp.get("status"):
            target["last_error"] = ""
            target["last_error_code"] = ""
            target["last_error_at"] = 0
        else:
            msg = resp.get("msg", {}) if isinstance(resp, dict) else {}
            if isinstance(msg, dict):
                target["last_error"] = str(msg.get("message", "") or "握手接收失败")
                target["last_error_code"] = str(msg.get("code", "") or "ERR_HANDSHAKE_ACCEPT")
            else:
                target["last_error"] = str(msg or "握手接收失败")
                target["last_error_code"] = "ERR_HANDSHAKE_ACCEPT"
            target["last_error_at"] = self._now()
            self._append_log("handshake", "token={} 接收失败: {} ({})".format(token, target["last_error"], target["last_error_code"]))
        self._audit(data, "replica_accept_handshake", {
            "token": token,
            "status": target["status"],
            "accept_attempts": target.get("accept_attempts", 0),
            "last_error": target.get("last_error", ""),
            "last_error_code": target.get("last_error_code", ""),
        })
        self._save_config(data)
        return resp

    def handshake_status(self, get):
        if not hasattr(get, "token"):
            return self._fail("缺少参数: token", "ERR_PARAM_REQUIRED")
        token = str(get.token).strip()
        data = self._load_config()
        for s in data.get("handshake_sessions", []):
            if s.get("token") == token:
                if s.get("status") == "pending" and int(s.get("expires_at", 0) or 0) < self._now():
                    s["status"] = "expired"
                    if not s.get("last_error"):
                        s["last_error"] = "握手已过期，请在主库重新创建"
                    if not s.get("last_error_code"):
                        s["last_error_code"] = "ERR_HANDSHAKE_EXPIRED"
                    if not int(s.get("last_error_at", 0) or 0):
                        s["last_error_at"] = self._now()
                    self._save_config(data)
                return self._ok(self._handshake_status_payload(s), "握手状态获取成功", "HANDSHAKE_STATUS_OK")
        return self._fail("未找到该握手令牌", "ERR_HANDSHAKE_NOT_FOUND")

    def handshake_overview(self, get=None):
        data = self._load_config()
        payload = self._handshake_overview_payload(data.get("handshake_sessions", []) or [])
        return self._ok(payload, "握手统计获取成功", "HANDSHAKE_OVERVIEW_OK")
