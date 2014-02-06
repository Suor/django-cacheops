from functools import wraps


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


