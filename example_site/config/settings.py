import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LANDING_PAGE = BASE_DIR.parent / "index.html"


class Settings:
    base_dir = BASE_DIR
    secret_key = os.getenv("MANGOO_SECRET_KEY", "change-this-in-production")
    debug = os.getenv("MANGOO_DEBUG", "true").lower() == "true"
    allowed_hosts = ["127.0.0.1", "localhost"]

    installed_apps = []
    middleware = []
    root_urlconf = "config.routes"

    class database:
        engine = "sqlite3"
        name = BASE_DIR / "mangoo.sqlite3"

    class server:
        host = "127.0.0.1"
        port = 8000

    class security:
        allowed_origins = []
        rate_limit = 120
        rate_window_seconds = 60
        security_headers = True
        force_https = False
        trust_proxy_headers = False
        max_request_bytes = 1_048_576


settings = Settings()
