"""WSGI entry point for production servers -- gunicorn, waitress, uWSGI, etc.

`hayavega launch`/`hayavega.py launch` is for development only; point a real
server at the `application` object below instead. For example:

    gunicorn config.wsgi:application --workers 5 --bind 0.0.0.0:8000   # Linux/macOS
    waitress-serve --port=8000 config.wsgi:application                 # any platform, incl. Windows

See DEPLOYMENT.md for worker-count guidance and the gunicorn/waitress split.
"""

from hayavega.conf import load_settings
from hayavega.hardening import HardeningOptions
from hayavega.middleware import load_middleware
from hayavega.runtime import create_application

settings = load_settings("config.settings")
urls = __import__(settings.root_urlconf, fromlist=["urlpatterns"])

application = create_application(
    urls.urlpatterns,
    hardening=HardeningOptions.from_settings(allowed_hosts=settings.allowed_hosts, security=settings.security),
    debug=settings.debug,
    middleware=load_middleware(settings.middleware),
)
