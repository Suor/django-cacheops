#!/usr/bin/env python
import os, sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.settings'

from django.core.management import call_command

if len(sys.argv) >= 2 and not sys.argv[1].startswith('-'):
    names = 'tests.' + sys.argv[1]
else:
    names = 'tests'
call_command('test', names, failfast='-x' in sys.argv)
