#!/usr/bin/env python
import os, sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.settings'

from django.core.management import call_command

names = sys.argv[1] if len(sys.argv) >= 2 else None
call_command('test', 'tests.' + names if names else 'tests')
