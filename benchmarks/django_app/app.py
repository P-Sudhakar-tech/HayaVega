"""Django comparison app: JSON echo, DB-backed list, template render, static file.

A single-file Django app (via settings.configure()) to keep the benchmark's
shape consistent with the other two frameworks' single-file apps, rather
than a full manage.py/project layout.

Run standalone: waitress-serve --port=9003 benchmarks.django_app.app:application
Served for real benchmark runs by benchmarks/run.py.
"""

import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(tempfile.gettempdir()) / "django_benchmark.sqlite3"
SEED_COUNT = 20

import django  # noqa: E402 -- must follow BASE_DIR/DATABASE, which settings.configure() below needs
from django.conf import settings  # noqa: E402

settings.configure(
    DEBUG=False,
    ALLOWED_HOSTS=["*"],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(DATABASE)}},
    INSTALLED_APPS=["django.contrib.staticfiles"],
    TEMPLATES=[
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(BASE_DIR / "templates")],
            "APP_DIRS": False,
            "OPTIONS": {},
        }
    ],
    STATIC_URL="/static/",
    STATICFILES_DIRS=[str(BASE_DIR / "static")],
    ROOT_URLCONF=__name__,
    SECRET_KEY="benchmark-only-not-for-production",
    USE_TZ=True,
)
django.setup()

from django.db import connection, models  # noqa: E402 -- must follow django.setup() above
from django.http import JsonResponse  # noqa: E402
from django.shortcuts import render  # noqa: E402
from django.urls import path  # noqa: E402
from django.views.static import serve as serve_static  # noqa: E402


class Post(models.Model):
    title = models.CharField(max_length=200)
    views = models.IntegerField()

    class Meta:
        app_label = "benchmark"


def seed() -> None:
    with connection.schema_editor() as editor:
        if Post._meta.db_table not in connection.introspection.table_names():
            editor.create_model(Post)
    if Post.objects.count() < SEED_COUNT:
        Post.objects.bulk_create([Post(title=f"Post {i}", views=i * 3) for i in range(SEED_COUNT)])


def _with_content_length(response):
    """Django's non-streaming HttpResponse doesn't set Content-Length itself --
    it relies on the WSGI server to frame the response (dev-server-only WSGI
    servers based on wsgiref compute it for you; waitress does not, and
    without it falls back to chunked encoding + Connection: close, silently
    disabling keep-alive). Set it explicitly so Django gets the same
    keep-alive treatment from waitress that Hayavega's Response already does
    by declaring its own Content-Length -- otherwise this benchmark would
    measure "chunked, non-keep-alive Django" against "keep-alive Hayavega",
    which is a WSGI response-framing artifact, not a framework difference.
    """
    response["Content-Length"] = str(len(response.content))
    return response


def echo(request):
    return _with_content_length(JsonResponse({"status": "ok"}))


def posts_json(request):
    rows = list(Post.objects.order_by("id")[:SEED_COUNT].values("id", "title", "views"))
    return _with_content_length(JsonResponse(rows, safe=False))


def posts_page(request):
    posts = list(Post.objects.order_by("id")[:SEED_COUNT].values("title", "views"))
    return _with_content_length(render(request, "page.html", {"posts": posts}))


def static_file(request, path_):
    return serve_static(request, path_, document_root=str(BASE_DIR / "static"))


urlpatterns = [
    path("echo", echo),
    path("posts.json", posts_json),
    path("posts", posts_page),
    path("static/<path:path_>", static_file),
]


def build_application():
    seed()
    from django.core.handlers.wsgi import WSGIHandler

    return WSGIHandler()


application = build_application()

if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    print("Django benchmark app (dev server): http://127.0.0.1:9003/")
    make_server("127.0.0.1", 9003, application).serve_forever()
