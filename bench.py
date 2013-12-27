#!/usr/bin/env python
import os, time, gc
os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.settings'

verbosity = 1
interactive = False
fixtures = ['basic']


from operator import itemgetter


def run_benchmarks(tests):
    for name, test in tests:
        time, clock = bench_test(test)
        print '%s\ttime: %.2fms\tclock: %.2fms' % (name, time * 1000, clock * 1000)

def bench_test(test):
    prepared = None
    if 'prepare_once' in test:
        prepared = test['prepare_once']()

    total = 0
    n = 1
    while total < 2:
        gc.disable()
        l = [bench_once(test, prepared) for i in range(n)]
        gc.enable()

        total = sum(r[0] for r in l)
        # print [int(x * 1000000) for x in [total / n, min(l), max(l),
        #                                   sum(norm) / len(norm), min(norm), max(norm)]]
        n *= 2

    # print len(l)
    s = sorted(l)
    norm = s[n/8:-n/8] if n / 8 else s

    norm_time = [r[0] for r in l]
    norm_clock = [r[1] for r in l]

    # return total * 2 / n # Or use normalized?
    return sum(norm_time) / len(norm_time), sum(norm_clock) / len(norm_clock)

def bench_once(test, prepared=None):
    if 'prepare' in test:
        prepared = test['prepare']()
    start = time.time()
    clock = time.clock()
    if prepared is None:
        test['run']()
    else:
        test['run'](prepared)
    return (time.time() - start, time.clock() - clock)

from django.db import connection
from django.core.management import call_command

# Create a test database.
db_name = connection.creation.create_test_db(verbosity=verbosity, autoclobber=not interactive)
# Import the fixture data into the test database.
call_command('loaddata', *fixtures, **{'verbosity': verbosity})

from tests.bench import TESTS
run_benchmarks(TESTS)

connection.creation.destroy_test_db(db_name, verbosity=verbosity)





