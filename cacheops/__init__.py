VERSION = (0, 9, 3)
__version__ = '.'.join(map(str, VERSION))

from .simple import *
from .query import *
from .invalidation import *

install_cacheops()
