import django.dispatch

cache_read = django.dispatch.Signal(providing_args=["func", "hit"])
invalidation_all = django.dispatch.Signal(providing_args=[])
invalidation_model = django.dispatch.Signal(providing_args=[])
invalidation_obj = django.dispatch.Signal(providing_args=["obj"])
invalidation_dict = django.dispatch.Signal(providing_args=["obj_dict"])
