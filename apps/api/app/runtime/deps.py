from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import DatabaseSession
from app.runtime.security import RuntimePrincipal, authenticate_runtime_token

_bearer = HTTPBearer(auto_error=False)


def require_runtime_scope(scope: str):
    def dependency(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
        db: DatabaseSession,
    ) -> RuntimePrincipal:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "runtime_credential_invalid", "message": "缺少运行时凭据"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return authenticate_runtime_token(db, credentials.credentials, scope)

    return dependency
