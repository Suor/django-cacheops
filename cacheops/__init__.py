VERSION = (1, 1, 0)
__version__ = '.'.join(map(str, VERSION if VERSION[-1] else VERSION[:2]))

from .simple import *
from .query import *
from .invalidation import *

install_cacheops()
