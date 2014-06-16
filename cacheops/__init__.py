VERSION = (2, 0)
__version__ = '.'.join(map(str, VERSION if VERSION[-1] else VERSION[:2]))


from django.conf import settings
FAKE = getattr(settings, 'CACHEOPS_FAKE', False)

if not FAKE:
    from .simple import *
    from .query import *
    from .invalidation import *
else:
    from .fake import *


install_cacheops()
