"""Key 掩码与混淆存储。

独立成模块：管理端配置（routers/admin.py）与业务侧真实调用（app/llm.py）
都需要解码存储态密钥，避免业务模块反向依赖管理端路由。
"""

import base64

from app.config import settings


def mask_key(key: str) -> str:
    """sk-1234567890abcd -> sk-****abcd；空 Key 返回空串。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:3]}****{key[-4:]}"


def obfuscate(key: str) -> str:
    """存储态混淆（XOR + base64）。

    注意：这是**混淆而非加密**，仅避免明文落库/落日志。生产必须换 KMS 或
    Fernet（cryptography），密钥由环境注入且不进代码仓（见 config.key_secret）。
    """
    if not key:
        return ""
    secret = settings.key_secret.encode()
    xored = bytes(b ^ secret[i % len(secret)] for i, b in enumerate(key.encode()))
    return base64.urlsafe_b64encode(xored).decode()


def deobfuscate(stored: str) -> str:
    if not stored:
        return ""
    secret = settings.key_secret.encode()
    raw = base64.urlsafe_b64decode(stored.encode())
    return bytes(b ^ secret[i % len(secret)] for i, b in enumerate(raw)).decode()
