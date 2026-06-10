from __future__ import annotations

import os
import secrets
from urllib.parse import quote

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings


def install_session_middleware(app, settings: Settings) -> None:
    secret = os.getenv(settings.web.session_secret_env) or os.getenv("WEB_PASSWORD")
    if not secret:
        secret = secrets.token_urlsafe(32)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie=settings.web.session_cookie_name,
        max_age=settings.web.session_max_age_seconds,
        same_site=settings.web.session_cookie_same_site,
        https_only=settings.web.session_cookie_secure,
    )


async def require_web_auth(request: Request) -> str:
    user = request.session.get("web_user")
    if isinstance(user, str) and user:
        return user
    next_url = quote(str(request.url.path), safe="")
    raise _RedirectToLogin(f"/login?next={next_url}")


async def require_api_auth(request: Request) -> str:
    user = request.session.get("web_user")
    if isinstance(user, str) and user:
        return user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


def verify_credentials(username: str, password: str) -> bool:
    expected_username = os.getenv("WEB_USERNAME")
    expected_password = os.getenv("WEB_PASSWORD")
    if not expected_username or not expected_password:
        return False
    return secrets.compare_digest(username, expected_username) and secrets.compare_digest(
        password, expected_password
    )


def login_user(request: Request, username: str) -> None:
    request.session["web_user"] = username


def logout_user(request: Request) -> None:
    request.session.clear()


def web_credentials_configured() -> bool:
    username = os.getenv("WEB_USERNAME")
    password = os.getenv("WEB_PASSWORD")
    return bool(username and password)


class _RedirectToLogin(HTTPException):
    def __init__(self, location: str) -> None:
        super().__init__(status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        self.location = location


async def redirect_auth_exception_handler(_, exc: _RedirectToLogin) -> RedirectResponse:
    return RedirectResponse(exc.location, status_code=status.HTTP_303_SEE_OTHER)
