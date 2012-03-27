#!/usr/bin/env python
import os, time, gc
os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.settings'

verbosity = 1
interactive = False
fixtures = ['basic']


def run_benchmarks(tests):
    for name, test in reversed(tests):
        elapsed = bench_test(test)
        print '%s %.2fms' % (name, elapsed * 1000)

def bench_test(test):
    if 'prepare_once' in test:
        test['prepare_once']()

    total = 0
    n = 1
    while total < 2:
        gc.disable()
        l = [bench_once(test) for i in range(n)]
        gc.enable()

        total = sum(l)
        s = sorted(l)
        norm = s[n/8:-n/8] if n / 8 else s
        print [int(x * 1000000) for x in [total / n, min(l), max(l),
                                          sum(norm) / len(norm), min(norm), max(norm)]]
        n *= 2

    return total * 2 / n # Or use normalized?

def bench_once(test):
    if 'prepare' in test:
        test['prepare']()
    start = time.time()
    test['run']()
    return time.time() - start

from django.db import connection
from django.core.management import call_command

# Create a test database.
db_name = connection.creation.create_test_db(verbosity=verbosity, autoclobber=not interactive)
# Import the fixture data into the test database.
call_command('loaddata', *fixtures, **{'verbosity': verbosity})

from tests.bench import TESTS
run_benchmarks(TESTS)

connection.creation.destroy_test_db(db_name, verbosity=verbosity)





