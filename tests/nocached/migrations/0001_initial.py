# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tests', '0002_one'),
    ]

    operations = [
        migrations.CreateModel(
            name='NonCachedVideoProxy',
            fields=[
            ],
            options={
                'proxy': True,
            },
            bases=('tests.video',),
        ),
    ]
