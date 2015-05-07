from tests.models import Video


class NonCachedVideoProxy(Video):
    class Meta:
        proxy = True
