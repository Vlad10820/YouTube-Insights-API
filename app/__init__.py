
# Allow running with `gunicorn app:app` by exporting the FastAPI instance.
from .main import app  # noqa: F401

