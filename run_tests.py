#!/usr/bin/env python
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.settings'

from django.core.management import call_command


def main():
    # from django.test import simple
    # simple.TEST_MODULE = 'bench'

    # Fire off the tests
    call_command('test', 'tests')


if __name__ == '__main__':
    main()
