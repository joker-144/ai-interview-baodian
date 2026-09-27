"""C 端鉴权：账号密码（pbkdf2 哈希）+ JWT（HS256）。

一期落地：注册 / 登录 / 当前用户，其余入口（短信验证码、微信 code）在
routers/auth.py 中预留 501 占位，二期按同一 token 协议接入。
密码用标准库 hashlib.pbkdf2_hmac（无需额外依赖），JWT 用 PyJWT。
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app import store

_PBKDF2_ITERATIONS = 120_000
_ALGORITHM = "HS256"

_bearer = HTTPBearer(auto_error=False)


# ---------------- 密码哈希 ----------------


def hash_password(password: str) -> str:
    """格式：pbkdf2_sha256$<iterations>$<salt-hex>$<hash-hex>（盐随机，校验时按存储参数重算）。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _scheme, iterations, salt_hex, _hash_hex = stored.split("$", 3)
        if _scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, AttributeError):
        return False
    return secrets.compare_digest(digest.hex(), _hash_hex)


# ---------------- JWT ----------------


def create_access_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=settings.jwt_expire_days),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_token(token: str) -> str:
    """校验签名与有效期，返回 user_id；失败抛 401（信息不区分过期/伪造，避免可探测）。"""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="登录凭证无效，请重新登录")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="登录凭证无效，请重新登录")
    return user_id


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """用户维度端点的统一依赖：校验 Bearer JWT → 懒加载该用户的内存态 → 返回 user_id。"""
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录：请先登录后再操作")
    user_id = decode_token(credentials.credentials)
    store.ensure_user(user_id)
    return user_id
