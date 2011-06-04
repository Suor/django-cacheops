# -*- coding: utf-8 -*-
from copy import deepcopy
import redis

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


profile_defaults = {
    'ops': (),
    'local_get': False
}
profiles = {
    'just_enable': {},
    'all': {'ops': ('get', 'fetch', 'count')},
    'get': {'ops': ('get',)},
    'count': {'ops': ('count',)},
}
for key in profiles:
    profiles[key] = dict(profile_defaults, **profiles[key])
model_profiles = {}

# Создаём соединение с редисом
try:
    redis_conf = settings.CACHEOPS_REDIS
except AttributeError:
    raise ImproperlyConfigured('You must specify non-empty CACHEOPS_REDIS setting to use cacheops')
redis_conn = redis.Redis(**redis_conf)


def prepare_profiles():
    """
    Готовит словарь app -> model -> profile, чтобы их быстро заполнять при создании queryset-ов.
    Таймаут тоже записываем в профиль.
    Кроме того, соединяется с редисом.
    """
    if hasattr(settings, 'CACHEOPS_PROFILES'):
        profiles.update(settings.CACHEOPS_PROFILES)

    ops = getattr(settings, 'CACHEOPS', {})
    for app_model, profile in ops.items():
        app, model = app_model.rsplit('.', 1)
        profile_name, timeout = profile[:2]

        if app not in model_profiles:
            model_profiles[app] = {}
        try:
            model_profiles[app][model] = mp = deepcopy(profiles[profile_name])
        except KeyError:
            raise ImproperlyConfigured('Unknown cacheops profile "%s"' % profile_name)

        if len(profile) > 2:
            mp.update(profile[2])
        mp['timeout'] = timeout
        mp['ops'] = set(mp['ops'])

    if not model_profiles and not settings.DEBUG:
        raise ImproperlyConfigured('You must specify non-empty CACHEOPS setting to use cacheops')


def model_profile(model):
    """
    Возвращает профиль по переданной модели
    """
    if not model_profiles:
        prepare_profiles()

    app, model_name = model._meta.app_label, model._meta.module_name
    if app not in model_profiles:
        return None
    else:
        app_profiles = model_profiles[app]
        return app_profiles.setdefault(model_name, app_profiles.get('*', None))
