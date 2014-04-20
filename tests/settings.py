import os

INSTALLED_APPS = [
    'cacheops' if os.environ.get('CACHEOPS') != 'FAKE' else 'fakecacheops',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'tests',
]

AUTH_PROFILE_MODULE = 'tests.UserProfile'

# Django replaces this, but it still wants it. *shrugs*
DATABASE_ENGINE = 'django.db.backends.sqlite3',
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': 'sqlite.db'
    },
    'slave': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': 'sqlite.db'
    }
}

CACHEOPS_REDIS = {
    'host': 'localhost',
    'port': 6379,
    'db': 13,
    'socket_timeout': 3,
}
CACHEOPS = {
    'tests.local': ('just_enable', 60*60, {'local_get': True}),
    'tests.cacheonsavemodel': ('just_enable', 60*60, {'cache_on_save': True}),
    'tests.dbbinded': ('just_enable', 60*60, {'db_agnostic': False}),
    'tests.issue': ('all', 60*60),
    'tests.genericcontainer': ('all', 60*60),
    '*.*': ('just_enable', 60*60),
}

# We need to catch any changes in django
CACHEOPS_STRICT_STRINGIFY = True

SECRET_KEY = 'abc'
