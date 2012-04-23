#!/usr/bin/env python
import os, sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.settings'

from django.core.management import call_command

call_command(*sys.argv[1:])
