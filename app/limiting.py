from slowapi import Limiter
from slowapi.util import get_remote_address

from .settings import settings


def _rate_limit_key(request):  # noqa: ANN001
	rapid_key = request.headers.get("X-RapidAPI-Key")
	if rapid_key:
		return f"rapidapi:{rapid_key}"

	api_key = request.headers.get("X-API-Key")
	if api_key:
		return f"apikey:{api_key}"

	return get_remote_address(request)


limiter = Limiter(key_func=_rate_limit_key, default_limits=[settings.rate_limit_default])
