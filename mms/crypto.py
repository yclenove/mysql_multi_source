# coding: utf-8

import os
import json
import hmac
import base64
import hashlib
import uuid
import logging

import public

logger = logging.getLogger("mms.crypto")

try:
    from cryptography.fernet import Fernet
    _HAS_FERNET = True
except Exception:
    _HAS_FERNET = False
    logger.warning(
        "未检测到 cryptography 库，密码加密功能不可用。"
        "请安装: pip install cryptography"
    )


class CryptoMixin(object):
    def _crypto_key(self):
        """Lazily fetch or create a crypto key file (chmod 600)."""
        self._ensure_dirs()
        if os.path.exists(self.crypto_key_path):
            raw = public.ReadFile(self.crypto_key_path) or ""
            key = raw.strip()
            if key:
                return key.encode("utf-8")
        if _HAS_FERNET:
            key = Fernet.generate_key().decode("utf-8")
        else:
            key = base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8")
        public.WriteFile(self.crypto_key_path, key)
        try:
            os.chmod(self.crypto_key_path, 0o600)
        except Exception:
            pass
        return key.encode("utf-8")

    def _crypto_encrypt(self, plaintext):
        if plaintext is None or plaintext == "":
            return ""
        text = str(plaintext)
        if text.startswith(self.CRYPTO_PREFIX):
            return text
        if not _HAS_FERNET:
            raise RuntimeError(
                "加密失败：未安装 cryptography 库，请执行 pip install cryptography"
            )
        try:
            token = Fernet(self._crypto_key()).encrypt(text.encode("utf-8")).decode("utf-8")
            return self.CRYPTO_PREFIX + token
        except Exception as ex:
            raise RuntimeError("加密失败: {}".format(ex))

    def _crypto_decrypt(self, value):
        if value is None or value == "":
            return ""
        text = str(value)
        if not text.startswith(self.CRYPTO_PREFIX):
            return text
        body = text[len(self.CRYPTO_PREFIX):]
        if body.startswith("xor:"):
            key = hashlib.sha256(self._crypto_key()).digest()
            raw = base64.urlsafe_b64decode(body[4:].encode("utf-8"))
            out = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
            return out.decode("utf-8", errors="replace")
        if _HAS_FERNET:
            try:
                return Fernet(self._crypto_key()).decrypt(body.encode("utf-8")).decode("utf-8")
            except Exception:
                return ""
        return ""

    def _sign_secret(self):
        self._ensure_dirs()
        if os.path.exists(self.sign_secret_path):
            return (public.ReadFile(self.sign_secret_path) or "").strip()
        secret = uuid.uuid4().hex + uuid.uuid4().hex
        public.WriteFile(self.sign_secret_path, secret)
        return secret

    def _profile_sign(self, payload):
        secret = self._sign_secret().encode("utf-8")
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hmac.new(secret, raw, hashlib.sha256).hexdigest()

    def _profile_verify(self, payload, signature):
        expect = self._profile_sign(payload)
        return hmac.compare_digest(expect, str(signature or ""))
