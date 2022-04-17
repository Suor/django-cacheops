import django.dispatch

cache_read = django.dispatch.Signal()  # args: func, hit
cache_invalidated = django.dispatch.Signal()  # args: obj_dict
