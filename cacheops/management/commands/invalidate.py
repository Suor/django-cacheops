# -*- coding: utf-8 -*-
from django.core.management.base import LabelCommand, CommandError
try:
    from django.apps import apps
except ImportError:
    # Django 1.7 like shim for older ones
    from django.db.models import get_app, get_model, get_models
    from django.core.exceptions import ImproperlyConfigured

    class AppConfig(object):
        def __init__(self, label, app):
            self.label, self.app = label, app

        def get_model(self, model_name):
            model = get_model(self.label, model_name)
            if not model:
                raise LookupError(
                    "App '%s' doesn't have a '%s' model." % (self.label, model_name))
            return model

        def get_models(self, include_auto_created=False):
            return get_models(self.app, include_auto_created=include_auto_created)

    class Apps(object):
        def get_app_config(self, app_label):
            try:
                return AppConfig(app_label, get_app(app_label))
            except ImproperlyConfigured as e:
                raise LookupError(*e.args)
    apps = Apps()

from cacheops.invalidation import *


class Command(LabelCommand):
    help = 'Invalidates cache for entire app, model or particular instance'
    args = '(all | <app> | <app>.<model> | <app>.<model>.<pk>) +'
    label = 'app or model or object'

    def handle_label(self, label, pk=None, **options):
        if label == 'all':
            self.handle_all()
        else:
            app_n_model = label.split('.')
            if len(app_n_model) == 1:
                self.handle_app(app_n_model[0])
            elif len(app_n_model) == 2:
                self.handle_model(*app_n_model)
            elif len(app_n_model) == 3:
                self.handle_obj(*app_n_model)
            else:
                raise CommandError('Wrong model/app name syntax: %s\n'
                                   'Type <app_name> or <app_name>.<model_name>' % label)

    def handle_all(self):
        invalidate_all()

    def handle_app(self, app_name):
        for model in self.get_app(app_name).get_models(include_auto_created=True):
            invalidate_model(model)

    def handle_model(self, app_name, model_name):
        invalidate_model(self.get_model(app_name, model_name))

    def handle_obj(self, app_name, model_name, obj_pk):
        model = self.get_model(app_name, model_name)
        try:
            obj = model.objects.get(pk=obj_pk)
        except model.DoesNotExist:
            raise CommandError('No %s.%s with pk = %s' % (app_name, model_name, obj_pk))
        invalidate_obj(obj)

    def get_app(self, app_name):
        try:
            return apps.get_app_config(app_name)
        except LookupError as e:
            raise CommandError(e)

    def get_model(self, app_name, model_name):
        try:
            return apps.get_app_config(app_name).get_model(model_name)
        except LookupError as e:
            raise CommandError(e)
