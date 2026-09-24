"""账号体系：手机号验证码 / 微信登录（一期 Mock，签发演示 token）。"""

from fastapi import APIRouter

from app import store
from app.schemas import PhoneLoginRequest, TokenResponse, UserProfile

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _token_response() -> TokenResponse:
    # 真实实现：校验验证码 / 微信 code 换 openid，签发 JWT
    return TokenResponse(token=f"mock-jwt-{store.new_id('t')}", user=UserProfile(**_user()))


def _user() -> dict:
    """资料种子叠加「我的」页修改项（注销后同样生效）。"""
    return {**store.USER, **store.state.profile}


@router.post("/phone-login", response_model=TokenResponse)
def phone_login(body: PhoneLoginRequest) -> TokenResponse:
    return _token_response()


@router.post("/wechat-login", response_model=TokenResponse)
def wechat_login() -> TokenResponse:
    return _token_response()


@router.get("/me", response_model=UserProfile)
def me() -> UserProfile:
    return UserProfile(**_user())
