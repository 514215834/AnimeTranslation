"""Fernet 加解密工具：渠道 API Key 落库加密（架构文档 4.1 / 非功能安全）。"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class CryptoError(ValueError):
    pass


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().fernet_key
    if not key:
        raise CryptoError("FERNET_KEY 未配置，无法加解密渠道密钥")
    return Fernet(key.encode())


def encrypt(secret: str) -> str:
    if not secret:
        return ""
    return _fernet().encrypt(secret.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise CryptoError("密钥解密失败：FERNET_KEY 已变更，请重新保存渠道 API Key") from e


def mask(secret: str) -> str:
    if len(secret) <= 8:
        return "*" * len(secret) if secret else ""
    return secret[:4] + "*" * (len(secret) - 8) + secret[-4:]
