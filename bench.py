#!/usr/bin/env python
from __future__ import print_function
import os, time, gc, sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.settings'

verbosity = 1
interactive = False
fixtures = ['basic']


HEADER_TEMPLATE = '==================== %-20s ===================='


def run_benchmarks(tests):
    for name, test in tests:
        if 'h' in flags:
            print(HEADER_TEMPLATE % name)
        time = bench_test(test)
        print('%-18s time: %.2fms' % (name, time * 1000))

def bench_test(test):
    prepared = None
    if 'prepare_once' in test:
        prepared = test['prepare_once']()
        if 'h' in flags:
                print('-' * 62)

    if 'p' in flags:
        test['run'] = profile(test['run'])

    total = 0
    n = 1
    while total < 2:
        gc.disable()
        durations = [bench_once(test, prepared) for i in range(n)]
        gc.enable()

        if '1' in flags:
            break

        total = sum(d for _, d in durations)
        n *= 2

    return min(d for d, _ in durations)

def bench_once(test, prepared=None):
    zero_start = time.time()
    if 'prepare' in test:
        prepared = test['prepare']()
        if 'h' in flags:
            print('-' * 62)
    start = time.time()
    if prepared is None:
        test['run']()
    else:
        test['run'](prepared)
    now = time.time()
    return now - start, now - zero_start

import django
from django.db import connection
from django.core.management import call_command

if hasattr(django, 'setup'):
    django.setup()

# Create a test database.
db_name = connection.creation.create_test_db(verbosity=verbosity, autoclobber=not interactive)
# Import the fixture data into the test database.
call_command('loaddata', *fixtures, **{'verbosity': verbosity})


flags = ''.join(arg[1:] for arg in sys.argv[1:] if arg.startswith('-'))
args = [arg for arg in sys.argv[1:] if not arg.startswith('-')]
selector = args[0] if args else ''
select = selector[1:].__eq__ if selector.startswith('=') else lambda s: selector in s

if 'p' in flags:
    from profilehooks import profile

from tests.bench import TESTS
try:
    if selector:
        tests = [(name, test) for name, test in TESTS if select(name)]
    else:
        tests = TESTS
    run_benchmarks(tests)
except KeyboardInterrupt:
    pass

connection.creation.destroy_test_db(db_name, verbosity=verbosity)
