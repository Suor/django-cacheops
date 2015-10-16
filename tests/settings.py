import os

INSTALLED_APPS = [
    'cacheops',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.admin',
    'tests',
]

ROOT_URLCONF = 'tests.urls'

MIDDLEWARE_CLASSES = []

AUTH_PROFILE_MODULE = 'tests.UserProfile'

# Django replaces this, but it still wants it. *shrugs*
DATABASE_ENGINE = 'django.db.backends.sqlite3',
if os.environ.get('CACHEOPS_DB') == 'postgresql':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql_psycopg2',
            'NAME': 'cacheops',
            'USER': 'cacheops',
            'PASSWORD': '',
            'HOST': ''
        },
        'slave': {
            'ENGINE': 'django.db.backends.postgresql_psycopg2',
            'NAME': 'cacheops_slave',
            'USER': 'cacheops',
            'PASSWORD': '',
            'HOST': ''
        },
    }
elif os.environ.get('CACHEOPS_DB') == 'postgis':
    POSTGIS_VERSION = (2, 1, 1)
    DATABASES = {
        'default': {
            'ENGINE': 'django.contrib.gis.db.backends.postgis',
            'NAME': 'cacheops',
            'USER': 'cacheops',
            'PASSWORD': '',
            'HOST': '',
        },
        'slave': {
            'ENGINE': 'django.contrib.gis.db.backends.postgis',
            'NAME': 'cacheops_slave',
            'USER': 'cacheops',
            'PASSWORD': '',
            'HOST': '',
        },
    }
elif os.environ.get('CACHEOPS_DB') == 'mysql':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': 'cacheops',
            'USER': 'cacheops',
            'PASSWORD': '',
            'HOST': '',
        },
        'slave': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': 'cacheops_slave',
            'USER': 'cacheops',
            'PASSWORD': '',
            'HOST': '',
        },
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': 'sqlite.db',
            # make in memory sqlite test db work with threads for python prior to 3.4
            # see https://code.djangoproject.com/ticket/12118
            'TEST': {
                'NAME': '/dev/shm/cacheo_sqlite.db'
            }
        },
        'slave': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': 'sqlite_slave.db',
            # make in memory sqlite test db work with threads for python prior to 3.4
            # see https://code.djangoproject.com/ticket/12118
            'TEST': {
                'NAME': '/dev/shm/cacheo_sqlite_slave.db'
            }
        }
    }

CACHEOPS_FAKE = os.environ.get('CACHEOPS') == 'FAKE'

CACHEOPS_REDIS = {
    'host': 'localhost',
    'port': 6379,
    'db': 13,
    'socket_timeout': 3,
}
CACHEOPS_DEFAULTS = {
    'timeout': 60*60
}
CACHEOPS = {
    'tests.local': {'local_get': True},
    'tests.cacheonsavemodel': {'cache_on_save': True},
    'tests.dbbinded': {'db_agnostic': False},
    'tests.genericcontainer': {'ops': ('fetch', 'get', 'count')},
    'tests.all': {'ops': 'all'},
    'tests.*': {},
    'tests.noncachedvideoproxy': None,
    'tests.noncachedmedia': None,
}

CACHEOPS_LRU = bool(os.environ.get('CACHEOPS_LRU'))
CACHEOPS_DEGRADE_ON_FAILURE = bool(os.environ.get('CACHEOPS_DEGRADE_ON_FAILURE'))
ALLOWED_HOSTS = ['testserver']

SECRET_KEY = 'abc'

# Required in Django 1.9
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates'}]
