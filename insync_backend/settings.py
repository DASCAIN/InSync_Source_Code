"""
Django settings for insync_backend project.
Merged: voice_tutor (main app) + RAG API settings.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

# Load env variables from .env
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Frontend directory (moved inside backend/)
FRONTEND_DIR = BASE_DIR / 'frontend'

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-insync-secret-key-for-dascain-2026')

DEBUG = os.getenv('DEBUG', 'True').lower() in ('true', '1', 'yes')

if not DEBUG and SECRET_KEY == 'django-insecure-insync-secret-key-for-dascain-2026':
    raise ImproperlyConfigured('SECRET_KEY must be set when DEBUG=False.')

def env_list(name, default=''):
    return [item.strip() for item in os.getenv(name, default).split(',') if item.strip()]

# ALLOWED_HOSTS = ['*']
# --- NEW CODE ---
ALLOWED_HOSTS = [
    'dascain.com',
    'www.dascain.com',
    'insync-backend-app-env.eba-2qbhpqh.ap-south-1.elasticbeanstalk.com',
    'localhost',
    '127.0.0.1',
    '*',
]

CSRF_TRUSTED_ORIGINS = [
    'https://dascain.com',
    'https://www.dascain.com',
    'https://insync-backend-app-env.eba-2qbhpqh.ap-south-1.elasticbeanstalk.com',
] + env_list('CSRF_TRUSTED_ORIGINS')
# ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', 'localhost,127.0.0.1',)
CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',
    'voice_tutor',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'insync_backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [FRONTEND_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'insync_backend.wsgi.application'
ASGI_APPLICATION = 'insync_backend.asgi.application'

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'insync_db',
        'USER': 'postgres',
        'PASSWORD': 'f$KZPJG!',
        'HOST': 'insync-aws-db.cjc24queim24.ap-south-1.rds.amazonaws.com',
        'PORT': '5432',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True

# Static files
STATIC_URL = '/static/'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = Path(os.getenv('MEDIA_ROOT', BASE_DIR / 'media'))

if DEBUG:
    STORAGES = {
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }
else:
    STORAGES = {
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
        },
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# CORS
CORS_ALLOW_ALL_ORIGINS = os.getenv('CORS_ALLOW_ALL_ORIGINS', str(DEBUG)).lower() in ('true', '1', 'yes')
CORS_ALLOWED_ORIGINS = env_list('CORS_ALLOWED_ORIGINS')
CORS_ALLOW_CREDENTIALS = True

# Session settings
SESSION_COOKIE_AGE = 1209600  # 2 weeks
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_SAVE_EVERY_REQUEST = True

# Production security settings
HTTPS_ENABLED = os.getenv('HTTPS', 'False').lower() in ('true', '1', 'yes')
SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', str(HTTPS_ENABLED)).lower() in ('true', '1', 'yes')
SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', str(HTTPS_ENABLED)).lower() in ('true', '1', 'yes')
CSRF_COOKIE_SECURE = os.getenv('CSRF_COOKIE_SECURE', str(HTTPS_ENABLED)).lower() in ('true', '1', 'yes')
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv('SECURE_HSTS_INCLUDE_SUBDOMAINS', 'False').lower() in ('true', '1', 'yes')
SECURE_HSTS_PRELOAD = os.getenv('SECURE_HSTS_PRELOAD', 'False').lower() in ('true', '1', 'yes')
X_FRAME_OPTIONS = 'DENY'
