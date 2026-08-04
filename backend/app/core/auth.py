"""轻量鉴权：Bearer Token（管理员），用户体系走 users 表。"""
import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Request

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 内存 token 表（重启失效；生产可迁 Redis）
_tokens: dict[str, dict] = {}


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return hmac.compare_digest(candidate, digest)


def issue_token(user_id: str, role: str) -> str:
    token = secrets.token_urlsafe(32)
    _tokens[token] = {"user_id": user_id, "role": role}
    return token


def revoke_token(token: str) -> None:
    _tokens.pop(token, None)


async def require_admin(
    request: Request, settings: Settings = Depends(get_settings)
) -> dict:
    """管理员鉴权：支持 Authorization Bearer 或 X-Admin-Token（兼容 .env 静态 token）。"""
    token = ""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    token = token or request.headers.get("X-Admin-Token", "")

    if not token:
        raise HTTPException(status_code=401, detail="缺少认证信息")

    # 静态管理 token（.env ADMIN_TOKEN）
    if settings.admin_token and hmac.compare_digest(token, settings.admin_token):
        return {"user_id": "admin", "role": "admin"}

    info = _tokens.get(token)
    if info and info["role"] == "admin":
        return info

    raise HTTPException(status_code=403, detail="无管理员权限")
