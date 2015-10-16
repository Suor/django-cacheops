import django.dispatch

post_lookup = django.dispatch.Signal(providing_args=["model", "hit_cache"])
