import pytest

from cacheops.redis import redis_client

from .models import User
from .utils import BaseTestCase


@pytest.fixture()
def base(db):
    case = BaseTestCase()
    case.setUp()
    yield
    case.tearDown()


def test_ttl(base):
    user = User.objects.create(username='Suor')
    qs = User.objects.cache(timeout=100).filter(pk=user.pk)
    list(qs)
    assert 90 <= redis_client.ttl(qs._cache_key()) <= 100
    assert redis_client.ttl(f'{qs._prefix}conj:auth_user:id={user.id}') > 100
