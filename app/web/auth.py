from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()


async def require_web_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    username = os.getenv("WEB_USERNAME")
    password = os.getenv("WEB_PASSWORD")
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WEB_USERNAME and WEB_PASSWORD must be configured.",
        )
    valid_username = secrets.compare_digest(credentials.username, username)
    valid_password = secrets.compare_digest(credentials.password, password)
    if not (valid_username and valid_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid web credentials.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
