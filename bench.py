#!/usr/bin/env python
import os, time, gc, sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.settings'

verbosity = 1
interactive = False
fixtures = ['basic']


from operator import itemgetter


def run_benchmarks(tests):
    for name, test in tests:
        time = bench_test(test)
        print('%-18s time: %.2fms' % (name, time * 1000))

def bench_test(test):
    prepared = None
    if 'prepare_once' in test:
        prepared = test['prepare_once']()

    total = 0
    n = 1
    while total < 2:
        gc.disable()
        durations = [bench_once(test, prepared) for i in range(n)]
        gc.enable()

        total = sum(d for _, d in durations)
        n *= 2

    return min(d for d, _ in durations)

def bench_once(test, prepared=None):
    zero_start = time.time()
    if 'prepare' in test:
        prepared = test['prepare']()
    start = time.time()
    if prepared is None:
        test['run']()
    else:
        test['run'](prepared)
    now = time.time()
    return now - start, now - zero_start

from django.db import connection
from django.core.management import call_command

# Create a test database.
db_name = connection.creation.create_test_db(verbosity=verbosity, autoclobber=not interactive)
# Import the fixture data into the test database.
call_command('loaddata', *fixtures, **{'verbosity': verbosity})

from tests.bench import TESTS
try:
    if len(sys.argv) > 1:
        tests = [(name, test) for name, test in TESTS if sys.argv[1] in name]
    else:
        tests = TESTS
    run_benchmarks(tests)
except KeyboardInterrupt:
    pass

connection.creation.destroy_test_db(db_name, verbosity=verbosity)
