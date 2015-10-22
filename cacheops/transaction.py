from cacheops.redis_client import redis_client
from django.db.transaction import get_connection


class AtomicMixIn(object):
    def __enter__(self):
        connection = get_connection(self.using)
        if not connection.in_atomic_block:
            redis_client.start_transaction()
        else:
            redis_client.start_savepoint()
        self._no_monkey.__enter__(self)

    def __exit__(self, exc_type, exc_value, traceback):
        self._no_monkey.__exit__(self, exc_type, exc_value, traceback)
        connection = get_connection(self.using)
        commit = not connection.closed_in_transaction and \
                      exc_type is None and \
                      not connection.needs_rollback
        if not connection.in_atomic_block:
            if commit:
                redis_client.commit_transaction()
            redis_client.end_transaction()
        else:
            if commit:
                redis_client.commit_savepoint()
            redis_client.end_savepoint()
