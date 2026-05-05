# coding: utf-8
"""Tests for mms/crypto.py"""

import pytest
from unittest.mock import patch, MagicMock
import hashlib
import base64


class TestCryptoEncrypt:
    """_crypto_encrypt: Fernet encryption, empty handling, idempotent prefix."""

    def test_encrypt_decrypt_roundtrip(self, plugin):
        """Encrypt then decrypt should yield the original plaintext."""
        plaintext = "my_secret_password_123"
        encrypted = plugin._crypto_encrypt(plaintext)
        assert encrypted.startswith(plugin.CRYPTO_PREFIX)
        decrypted = plugin._crypto_decrypt(encrypted)
        assert decrypted == plaintext

    def test_encrypt_empty_string(self, plugin):
        assert plugin._crypto_encrypt("") == ""

    def test_encrypt_none(self, plugin):
        assert plugin._crypto_encrypt(None) == ""

    def test_encrypt_already_prefixed(self, plugin):
        """If the value already has CRYPTO_PREFIX, it should be returned as-is."""
        already = plugin.CRYPTO_PREFIX + "sometoken"
        assert plugin._crypto_encrypt(already) == already

    def test_encrypt_non_string(self, plugin):
        """Non-string values are cast to str then encrypted."""
        encrypted = plugin._crypto_encrypt(12345)
        assert encrypted.startswith(plugin.CRYPTO_PREFIX)
        assert plugin._crypto_decrypt(encrypted) == "12345"

    @patch("mms.crypto._HAS_FERNET", False)
    def test_encrypt_no_fernet_raises(self, plugin):
        """When cryptography is not installed, encrypt should raise RuntimeError."""
        with pytest.raises(RuntimeError, match="未安装 cryptography"):
            plugin._crypto_encrypt("secret")

    def test_different_plaintexts_different_ciphertexts(self, plugin):
        """Two different plaintexts should produce different ciphertexts."""
        e1 = plugin._crypto_encrypt("alpha")
        e2 = plugin._crypto_encrypt("beta")
        assert e1 != e2


class TestCryptoDecrypt:
    """_crypto_decrypt: Fernet decryption, XOR backward compat, empty handling."""

    def test_decrypt_empty(self, plugin):
        assert plugin._crypto_decrypt("") == ""

    def test_decrypt_none(self, plugin):
        assert plugin._crypto_decrypt(None) == ""

    def test_decrypt_no_prefix_returns_as_is(self, plugin):
        """If value doesn't start with CRYPTO_PREFIX, return it unchanged."""
        assert plugin._crypto_decrypt("plain_text") == "plain_text"

    def test_decrypt_invalid_fernet_token(self, plugin):
        """Invalid Fernet token under the prefix should return empty string."""
        bad = plugin.CRYPTO_PREFIX + "not-a-valid-fernet-token"
        assert plugin._crypto_decrypt(bad) == ""

    def test_xor_backward_compat(self, plugin):
        """XOR-encrypted values (xor: prefix) should still be decryptable."""
        plaintext = "old_password"
        key = hashlib.sha256(plugin._crypto_key()).digest()
        raw = plaintext.encode("utf-8")
        xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
        encoded_body = "xor:" + base64.urlsafe_b64encode(xored).decode("utf-8")
        encrypted = plugin.CRYPTO_PREFIX + encoded_body
        result = plugin._crypto_decrypt(encrypted)
        assert result == plaintext

    @patch("mms.crypto._HAS_FERNET", False)
    def test_decrypt_fernet_body_no_lib_returns_empty(self, plugin):
        """When Fernet is missing and token is Fernet-style, return empty."""
        # Create a valid Fernet token first (will need to temporarily enable)
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        token = Fernet(key).encrypt(b"test").decode("utf-8")
        encrypted = plugin.CRYPTO_PREFIX + token
        # Now patch _crypto_key to return the same key
        with patch.object(type(plugin), '_crypto_key', return_value=key):
            assert plugin._crypto_decrypt(encrypted) == ""

    def test_roundtrip_with_long_password(self, plugin):
        """Long passwords should roundtrip correctly."""
        long_pwd = "A" * 500 + "!@#$%^&*()" + "b" * 500
        assert plugin._crypto_decrypt(plugin._crypto_encrypt(long_pwd)) == long_pwd

    def test_roundtrip_unicode(self, plugin):
        """Unicode passwords should roundtrip correctly."""
        unicode_pwd = "密码パスワード contraseña"
        assert plugin._crypto_decrypt(plugin._crypto_encrypt(unicode_pwd)) == unicode_pwd


class TestSignAndVerify:
    """_profile_sign / _profile_verify: HMAC signing and verification."""

    def test_sign_and_verify(self, plugin):
        payload = {"source_id": "s1", "channel_name": "ch1"}
        sig = plugin._profile_sign(payload)
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA-256 hex digest length
        assert plugin._profile_verify(payload, sig) is True

    def test_verify_wrong_signature(self, plugin):
        payload = {"source_id": "s1"}
        assert plugin._profile_verify(payload, "wrong_sig") is False

    def test_verify_tampered_payload(self, plugin):
        payload = {"source_id": "s1"}
        sig = plugin._profile_sign(payload)
        payload["source_id"] = "tampered"
        assert plugin._profile_verify(payload, sig) is False

    def test_verify_empty_signature(self, plugin):
        payload = {"source_id": "s1"}
        assert plugin._profile_verify(payload, "") is False

    def test_verify_none_signature(self, plugin):
        payload = {"source_id": "s1"}
        assert plugin._profile_verify(payload, None) is False

    def test_sign_deterministic(self, plugin):
        """Same payload should produce the same signature."""
        payload = {"a": 1, "b": 2}
        assert plugin._profile_sign(payload) == plugin._profile_sign(payload)

    def test_sign_key_order_independent(self, plugin):
        """Signature should be order-independent (sort_keys=True)."""
        sig1 = plugin._profile_sign({"a": 1, "b": 2})
        sig2 = plugin._profile_sign({"b": 2, "a": 1})
        assert sig1 == sig2


class TestSignSecret:
    """_sign_secret: create / read signing secret."""

    def test_creates_secret_file(self, plugin):
        secret = plugin._sign_secret()
        assert isinstance(secret, str)
        assert len(secret) == 64  # two uuid4 hex = 64 chars

    def test_returns_same_secret_on_subsequent_calls(self, plugin):
        s1 = plugin._sign_secret()
        s2 = plugin._sign_secret()
        assert s1 == s2

    def test_reads_existing_secret(self, plugin):
        """If secret file already exists, read from it."""
        import os
        os.makedirs(plugin.plugin_root, exist_ok=True)
        with open(plugin.sign_secret_path, "w") as f:
            f.write("my_custom_secret_abc123")
        assert plugin._sign_secret() == "my_custom_secret_abc123"
