from mangoo import Response, admin_routes, api_route, path, static_routes
from mangoo.auth_api import auth_routes
from mangoo.graphql import Schema, graphql_view
from .settings import DATABASES, DEBUG, LANDING_PAGE

def home(request):
    return Response(LANDING_PAGE.read_text(encoding='utf-8'))

def health(request):
    return {'status': 'ok', 'service': 'Mangoo REST API'}

schema = Schema(query={'greeting': lambda request: 'Hello from Mangoo GraphQL'})

urlpatterns = [
    path('', home),
    *static_routes(LANDING_PAGE.parent / 'assets', 'assets'),
    api_route('api/health', health),
    path('graphql', graphql_view(schema), methods=('POST',)),
    *auth_routes(DATABASES['default'], secure_cookies=not DEBUG),
    *admin_routes(DATABASES['default']),
]
