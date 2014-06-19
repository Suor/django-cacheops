#!/usr/bin/env python
import os, sys, re
os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.settings'

import django
from django.core.management import call_command

names_prefix = 'tests.tests' if django.VERSION >= (1, 6) else 'tests'
names = next((a for a in sys.argv[1:] if not a.startswith('-')), None)

if names and re.search(r'^\d+$', names):
    names = names_prefix + '.IssueTests.test_' + names
elif names and not names.startswith('tests.'):
    names = names_prefix + '.' + names
else:
    names = names_prefix

if hasattr(django, 'setup'):
    django.setup()
call_command('test', names, failfast='-x' in sys.argv)
