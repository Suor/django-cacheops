# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tests', '0002_one'),
    ]

    operations = [
        migrations.CreateModel(
            name='NonCachedMedia',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('title', models.CharField(max_length=128)),
            ],
        ),
        migrations.CreateModel(
            name='NonCachedVideoProxy',
            fields=[
            ],
            options={
                'proxy': True,
            },
            bases=('tests.video',),
        ),
        migrations.CreateModel(
            name='MediaProxy',
            fields=[
            ],
            options={
                'proxy': True,
            },
            bases=('tests.noncachedmedia',),
        ),
    ]
