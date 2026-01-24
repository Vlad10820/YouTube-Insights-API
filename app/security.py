from fastapi import Header, HTTPException, status

from .settings import settings


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_rapidapi_key: str | None = Header(default=None, alias="X-RapidAPI-Key"),
    x_rapidapi_proxy_secret: str | None = Header(default=None, alias="X-RapidAPI-Proxy-Secret"),
) -> None:
    if not settings.require_api_key:
        return

    # RapidAPI flow: provider typically cannot pre-validate per-user keys.
    # If a request arrives with X-RapidAPI-Key, treat it as authenticated, and optionally
    # require the proxy secret to ensure the call came through RapidAPI.
    if x_rapidapi_key:
        if settings.require_rapidapi_proxy_secret:
            if not settings.rapidapi_proxy_secret:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Server misconfigured: RAPIDAPI_PROXY_SECRET not set",
                )
            if x_rapidapi_proxy_secret != settings.rapidapi_proxy_secret:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid RapidAPI proxy secret",
                )
        return

    # Direct flow: static API_KEY.
    if not settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfigured: API_KEY not set",
        )
    if x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
