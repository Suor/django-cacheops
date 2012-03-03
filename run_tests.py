#!/usr/bin/env python
# from http://www.travisswicegood.com/2010/01/17/django-virtualenv-pip-and-fabric/

from django.conf import settings
from django.core.management import call_command


def main():
    # Dynamically configure the Django settings with the minimum necessary to
    # get Django running tests
    settings.configure(
        INSTALLED_APPS = [
            'cacheops',
            'tests',
        ],
        # Django replaces this, but it still wants it. *shrugs*
        DATABASE_ENGINE = 'django.db.backends.sqlite3',
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
            }
        },
        CACHEOPS_REDIS = {
            'host': 'localhost',
            'port': 6379,
            'db': 13,
            'socket_timeout': 3,
        },
        CACHEOPS = {
            '*.*': ('just_enable', 60*60),
        }
    )

    # Fire off the tests
    call_command('test', 'tests')


if __name__ == '__main__':
    main()
