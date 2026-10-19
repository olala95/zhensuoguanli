# -*- coding: utf-8 -*-
"""口令与令牌。无第三方依赖：hashlib.pbkdf2_hmac + secrets。"""

import hashlib
import hmac
import os
import secrets

ITERATIONS = 120000


def new_salt():
    return secrets.token_hex(16)


def hash_password(password, salt):
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             salt.encode("utf-8"), ITERATIONS)
    return dk.hex()


def verify_password(password, salt, expect_hash):
    return hmac.compare_digest(hash_password(password, salt), expect_hash or "")


def new_token():
    return secrets.token_hex(32)


def validate_password_policy(password):
    """口令强度：>=8 位，且不能是纯数字。返回 (ok, message)。"""
    if not password or len(password) < 8:
        return False, "口令长度至少 8 位"
    if password.isdigit():
        return False, "口令不能为纯数字"
    return True, ""


def safe_path(root, rel):
    """防止路径穿越：确保解析后的路径仍在 root 之内。"""
    full = os.path.normpath(os.path.join(root, rel))
    if not os.path.abspath(full).startswith(os.path.abspath(root)):
        return None
    return full
