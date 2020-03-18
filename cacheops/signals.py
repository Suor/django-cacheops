import django.dispatch

cache_read = django.dispatch.Signal(providing_args=["func", "hit"])
cache_invalidated = django.dispatch.Signal(providing_args=["obj_dict"])
