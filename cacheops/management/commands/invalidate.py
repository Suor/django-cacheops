# -*- coding: utf-8 -*-
from django.core.management.base import LabelCommand, CommandError
from django.core.exceptions import ImproperlyConfigured
from django.db.models import get_app, get_model, get_models

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
                raise CommandError('Wrong model/app name syntax: %s\nType <app_name> or <app_name>.<model_name>' % label)

    def handle_all(self):
        invalidate_all()

    def handle_app(self, app_name):
        try:
            app = get_app(app_name)
        except ImproperlyConfigured, e:
            raise CommandError(e)

        for model in get_models(app, include_auto_created=True):
            invalidate_model(model)

    def handle_model(self, app_name, model_name):
        model = get_model(app_name, model_name)
        if model is None:
            raise CommandError('Unknown model: %s.%s' % (app_name, model_name))
        invalidate_model(model)

    def handle_obj(self, app_name, model_name, obj_pk):
        model = get_model(app_name, model_name)
        if model is None:
            raise CommandError('Unknown model: %s.%s' % (app_name, model_name))
        try:
            obj = model.objects.get(pk=obj_pk)
        except model.DoesNotExist:
            raise CommandError('No %s.%s with pk = %s' % (app_name, model_name, obj_pk))
        invalidate_obj(obj)
