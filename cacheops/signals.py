import django.dispatch

cache_read = django.dispatch.Signal(providing_args=["func", "hit"])
