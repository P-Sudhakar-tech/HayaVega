"""WSGI entry point for production servers -- gunicorn, waitress, uWSGI, etc.

`mango launch`/`manage.py launch` is for development only; point a real
server at the `application` object below instead. For example:

    gunicorn project.wsgi:application --workers 5 --bind 0.0.0.0:8000   # Linux/macOS
    waitress-serve --port=8000 project.wsgi:application                 # any platform, incl. Windows

See DEPLOYMENT.md for worker-count guidance and the gunicorn/waitress split.
"""

from mangoo.conf import load_settings
from mangoo.hardening import HardeningOptions
from mangoo.runtime import create_application

settings = load_settings("project.settings")
urls = __import__(settings.root_urlconf, fromlist=["urlpatterns"])

application = create_application(
    urls.urlpatterns,
    hardening=HardeningOptions.from_settings(allowed_hosts=settings.allowed_hosts, security=settings.security),
    debug=settings.debug,
)
