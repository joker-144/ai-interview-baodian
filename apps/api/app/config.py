"""应用配置：通过环境变量覆盖，二期接入真实数据库 / LLM 时扩展。"""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    app_name: str = "AI 面试宝典 API"
    # 前端开发服务器来源（CORS 白名单）
    cors_origins: list[str] = field(
        default_factory=lambda: [
            o.strip()
            for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
            if o.strip()
        ]
    )
    # 二期预留：PostgreSQL 连接串（当前内存实现不使用）
    database_url: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://aib:aib@localhost:5432/aib")
    # 一期 Mock 开关：true 时解析/出题走模拟实现
    mock_llm: bool = os.getenv("MOCK_LLM", "1") == "1"
    # 管理端（/admin）访问令牌：请求头 X-Admin-Token 校验（文档 4.6.1：独立鉴权，不复用 C 端账号体系）
    admin_token: str = os.getenv("ADMIN_TOKEN", "admin-dev-token")
    # API Key 存储密钥：一期为 XOR+base64 混淆（非加密），生产需换 KMS / Fernet；密钥不入仓
    key_secret: str = os.getenv("KEY_SECRET", "aib-dev-key-secret-change-me")
    # 本地模型权重目录（离线推理）：由 scripts/download_models.py 下载，权重入仓跟踪
    local_models_dir: str = os.getenv(
        "LOCAL_MODELS_DIR", str(Path(__file__).resolve().parent.parent / "models")
    )
    # 本地推理线程数：受限环境（容器/低内存）下按核数预分配会直接 OOM，默认保守取 2
    local_model_threads: int = int(os.getenv("LOCAL_MODEL_THREADS", "2"))


settings = Settings()
