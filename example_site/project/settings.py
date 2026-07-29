from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
LANDING_PAGE = BASE_DIR.parent / 'index.html'
SECRET_KEY = os.getenv('MANGOO_SECRET_KEY', 'change-this-in-production')
DEBUG = os.getenv('MANGOO_DEBUG', 'true').lower() == 'true'
ALLOWED_HOSTS = ['127.0.0.1', 'localhost']

INSTALLED_APPS = []
MIDDLEWARE = []
ROOT_URLCONF = 'project.urls'

DATABASES = {
    'default': {
        'ENGINE': 'sqlite3',
        'NAME': BASE_DIR / 'mangoo.sqlite3',
    },
}

SERVER = {
    'HOST': '127.0.0.1',
    'PORT': 8000,
}

SECURITY = {
    'ALLOWED_ORIGINS': [],
    'RATE_LIMIT': 120,
    'RATE_WINDOW_SECONDS': 60,
    'SECURITY_HEADERS': True,
    'FORCE_HTTPS': False,
    'TRUST_PROXY_HEADERS': False,
    'MAX_REQUEST_BYTES': 1_048_576,
}
