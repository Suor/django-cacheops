#!/usr/bin/env python3
import os, time, gc, sys, shutil
from funcy import re_tester
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
        print('%-18s time: %.3fms' % (name, time * 1000))

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
        durations = [bench_once(test, prepared) for _ in range(n)]
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

django.setup()


# Parse command line arguments
flags = ''.join(arg[1:] for arg in sys.argv[1:] if arg.startswith('-'))
args = [arg for arg in sys.argv[1:] if not arg.startswith('-')]
selector = args[0] if args else ''
select = selector[1:].__eq__ if selector.startswith('=') else re_tester(selector)

if 'p' in flags:
    from profilehooks import profile


db_name = None
try:
    shutil.rmtree('tests/migrations', True)
    call_command('makemigrations', 'tests', verbosity=0)
    db_name = connection.creation.create_test_db(verbosity=verbosity, autoclobber=not interactive)
    call_command('loaddata', *fixtures, **{'verbosity': verbosity})

    from cacheops.redis import redis_client
    redis_client.flushdb()

    from tests.bench import TESTS  # import is here because it executes queries
    if selector:
        tests = [(name, test) for name, test in TESTS if select(name)]
    else:
        tests = TESTS
    run_benchmarks(tests)
except KeyboardInterrupt:
    pass
finally:
    if db_name:
        connection.creation.destroy_test_db(db_name, verbosity=verbosity)
    shutil.rmtree('tests/migrations')
