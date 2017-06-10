from funcy import cached_property
from django.core.exceptions import ImproperlyConfigured

from .conf import settings


def get_prefix(**kwargs):
    return settings.CACHEOPS_PREFIX(PrefixQuery(**kwargs))


class PrefixQuery(object):
    def __init__(self, **kwargs):
        assert set(kwargs) <= {'func', '_queryset', '_cond_dnfs', 'dbs', 'tables'}
        kwargs.setdefault('func', None)
        self.__dict__.update(kwargs)

    @cached_property
    def dbs(self):
        return [self._queryset.db]

    @cached_property
    def db(self):
        if len(self.dbs) > 1:
            dbs_str = ', '.join(self.dbs)
            raise ImproperlyConfigured('Single db required, but several used: ' + dbs_str)
        return self.dbs[0]

    @cached_property
    def _cond_dnfs(self):
        return self._queryset._cond_dnfs

    @cached_property
    def tables(self):
        # Not guaranteed to be unique
        return [table for table, _ in self._dnfs]

    @cached_property
    def table(self):
        tables = list(set(self.tables))
        if len(tables) > 1:
            tables_str = ', '.join(tables)
            raise ImproperlyConfigured('Single table required, but several used: ' + tables_str)
        return tables[0]
