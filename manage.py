#!/usr/bin/env python
import os, sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.settings'

import django
if hasattr(django, 'setup'):
    django.setup()

from django.core.management import call_command
call_command(*sys.argv[1:])
