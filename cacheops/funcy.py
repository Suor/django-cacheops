from functools import wraps


class cached_property(object):
    """
    Decorator that converts a method with a single self argument into a
    property cached on the instance.

    NOTE: implementation borrowed from Django.
    NOTE: we use fget, fset and fdel attributes to mimic @property.
    """
    fset = fdel = None

    def __init__(self, fget):
        self.fget = fget

    def __get__(self, instance, type=None):
        if instance is None:
            return self
        res = instance.__dict__[self.fget.__name__] = self.fget(instance)
        return res


def memoize(func):
    cache = {}

    @wraps(func)
    def wrapper(*args):
        try:
            return cache[args]
        except KeyError:
            cache[args] = func(*args)
            return cache[args]

    return wrapper
