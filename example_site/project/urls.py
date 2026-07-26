from mangoo import Response, api_route, path
from mangoo.auth_api import auth_routes
from mangoo.graphql import Schema, graphql_view
from .settings import DATABASES, LANDING_PAGE

def home(request):
    return Response(LANDING_PAGE.read_text(encoding='utf-8'))

def health(request):
    return {'status': 'ok', 'service': 'Mangoo REST API'}

schema = Schema(query={'greeting': lambda request: 'Hello from Mangoo GraphQL'})

urlpatterns = [
    path('', home),
    api_route('api/health', health),
    path('graphql', graphql_view(schema), methods=('POST',)),
    *auth_routes(DATABASES['default']),
]
