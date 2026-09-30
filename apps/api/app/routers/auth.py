"""账号体系：一期 = 账号密码（pbkdf2 + JWT）；短信 / 微信为二期预留接口。

签发的 token 为 HS256 JWT（sub=user_id，7 天有效），由 app.auth.get_current_user
在用户维度端点统一校验；登录 / 注册本身不要求已登录。
"""

import re

from fastapi import APIRouter, Depends, HTTPException

from app import db, store
from app.auth import get_current_user, hash_password, verify_password, create_access_token
from app.schemas import (
    PasswordLoginRequest,
    PhoneLoginRequest,
    RegisterRequest,
    TokenResponse,
    UserProfile,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_@\-\u4e00-\u9fa5]{2,64}$")


def _user_payload(user_id: str) -> UserProfile:
    """当前资料 = USER 种子被该用户的修改项叠加（注销后同样生效）。"""
    return UserProfile(**store.user_profile(user_id))


def _issue(user_id: str) -> TokenResponse:
    store.ensure_user(user_id)
    return TokenResponse(token=create_access_token(user_id), user=_user_payload(user_id))


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: RegisterRequest) -> TokenResponse:
    """注册：账号（用户名/手机号）+ 密码；建号后直接登录（注册即登录）。

    MySQL 可用时主写 users 表；库不可用回落内存账号表（store.accounts），
    重启即失（内存模式口径），登录同走双路查号。
    """
    account = body.account.strip()
    if not _ACCOUNT_RE.match(account):
        raise HTTPException(status_code=422, detail="账号仅支持中英文、数字、_@-，长度 2~64")
    if db.get_user_by_account(account) or store.find_account(account):
        raise HTTPException(status_code=409, detail="该账号已被注册")

    user_id = store.new_id("u")
    name = (body.name or account).strip()[:8] or account[:8]
    profile = {
        "name": name,
        "avatarText": name[0],
        "targetRole": "",
        "years": 0,
        "streak": 0,
        "totalAnswered": 0,
        "correctRate": 0,
        # 账号本身是手机号时直接作为回显（一期不脱敏存储，展示层已按协议脱敏）
        "phone": account if re.match(r"^1\d{10}$", account) else "",
        "wechatBound": False,
    }
    password_hash = hash_password(body.password)
    # 内存资料以注册项为底：保证 _issue → ensure_user 不会回退到种子资料（林晓）
    store.save_account(user_id, account, password_hash, profile)
    db.create_user(user_id, account, password_hash, profile,
                   dict(store.USER_SETTINGS_SEED), None)
    return _issue(user_id)


@router.post("/login", response_model=TokenResponse)
def login(body: PasswordLoginRequest) -> TokenResponse:
    """账号密码登录：校验 pbkdf2 哈希后签发 JWT（MySQL 未命中走内存账号兜底）。"""
    account = body.account.strip()
    record = db.get_user_by_account(account) or store.find_account(account)
    # 账号不存在与密码错误给同一提示，避免账号可被枚举探测
    if record is None or not record["passwordHash"] or not verify_password(
        body.password, record["passwordHash"]
    ):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    return _issue(record["userId"])


@router.post("/phone-login", response_model=TokenResponse)
def phone_login(body: PhoneLoginRequest) -> TokenResponse:
    """预留（二期接短信服务）：签名与参数保持稳定，接通后仅替换实现。"""
    raise HTTPException(status_code=501, detail="短信验证码登录暂未开通，请使用账号密码登录")


@router.post("/wechat-login", response_model=TokenResponse)
def wechat_login() -> TokenResponse:
    """预留（二期接微信开放平台 code2session）：同上。"""
    raise HTTPException(status_code=501, detail="微信登录暂未开通，请使用账号密码登录")


@router.get("/me", response_model=UserProfile)
def me(user_id: str = Depends(get_current_user)) -> UserProfile:
    return _user_payload(user_id)
