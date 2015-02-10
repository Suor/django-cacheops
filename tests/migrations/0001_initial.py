# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os

from django.db import models, migrations
import datetime
import tests.models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        # ('contenttypes', '0002_remove_content_type_name'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='All',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('tag', models.IntegerField(null=True)),
            ],
        ),
        migrations.CreateModel(
            name='Brand',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
            ],
        ),
        migrations.CreateModel(
            name='BrandT',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
            ],
        ),
        migrations.CreateModel(
            name='CacheOnSaveModel',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('title', models.CharField(max_length=32)),
            ],
        ),
        migrations.CreateModel(
            name='Category',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('title', models.CharField(max_length=128)),
            ],
        ),
        migrations.CreateModel(
            name='Contained',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('name', models.CharField(max_length=30)),
            ],
        ),
        migrations.CreateModel(
            name='DbAgnostic',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
            ],
        ),
        migrations.CreateModel(
            name='DbBinded',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
            ],
        ),
        migrations.CreateModel(
            name='Extra',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('tag', models.IntegerField(unique=True, db_column=b'custom_column_name')),
            ],
        ),
        migrations.CreateModel(
            name='GenericContainer',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('object_id', models.PositiveIntegerField()),
                ('name', models.CharField(max_length=30)),
                ('content_type', models.ForeignKey(to='contenttypes.ContentType')),
            ],
        ),
        migrations.CreateModel(
            name='Label',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('text', models.CharField(default=b'', max_length=127, blank=True)),
            ],
        ),
        migrations.CreateModel(
            name='Labeling',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('tag', models.IntegerField()),
                ('brand', models.ForeignKey(to='tests.BrandT')),
            ],
        ),
        migrations.CreateModel(
            name='LabelT',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('text', models.CharField(default=b'', max_length=127, blank=True)),
            ],
        ),
        migrations.CreateModel(
            name='Local',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('tag', models.IntegerField(null=True)),
            ],
        ),
        migrations.CreateModel(
            name='Media',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('name', models.CharField(max_length=128)),
            ],
        ),
        migrations.CreateModel(
            name='Point',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('x', models.DecimalField(default=0.0, max_digits=8, decimal_places=6, blank=True)),
            ],
        ),
        migrations.CreateModel(
            name='Post',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('title', models.CharField(max_length=128)),
                ('visible', models.BooleanField(default=True)),
                ('category', models.ForeignKey(related_name='posts', to='tests.Category')),
            ],
        ),
        migrations.CreateModel(
            name='Product',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('name', models.CharField(max_length=32)),
            ],
        ),
        migrations.CreateModel(
            name='ProductReview',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('status', models.IntegerField()),
                ('product', models.ForeignKey(related_name='reviews', to='tests.Product', null=True)),
            ],
        ),
        migrations.CreateModel(
            name='Profile',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('tag', models.IntegerField()),
                ('user', models.ForeignKey(to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='Video',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('title', models.CharField(max_length=128)),
            ],
        ),
        migrations.CreateModel(
            name='Weird',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('date_field', models.DateField(default=datetime.date(2000, 1, 1))),
                ('datetime_field', models.DateTimeField(default=datetime.datetime(2000, 1, 1, 10, 10))),
                ('time_field', models.TimeField(default=datetime.time(10, 10))),
                ('list_field', tests.models.IntegerArrayField(default=list)),
                ('custom_field', tests.models.CustomField(default=tests.models.custom_value_default)),
                ('binary_field', models.BinaryField()),
            ],
        ),
        migrations.CreateModel(
            name='Movie',
            fields=[
                ('media_ptr', models.OneToOneField(parent_link=True, auto_created=True, primary_key=True, serialize=False, to='tests.Media')),
                ('year', models.IntegerField()),
            ],
            bases=('tests.media',),
        ),
        migrations.CreateModel(
            name='PremiumBrand',
            fields=[
                ('brand_ptr', models.OneToOneField(parent_link=True, auto_created=True, primary_key=True, serialize=False, to='tests.Brand')),
                ('extra', models.CharField(default=b'', max_length=127, blank=True)),
            ],
            bases=('tests.brand',),
        ),
        migrations.AddField(
            model_name='labeling',
            name='label',
            field=models.ForeignKey(to='tests.LabelT'),
        ),
        migrations.AddField(
            model_name='extra',
            name='post',
            field=models.OneToOneField(to='tests.Post'),
        ),
        migrations.AddField(
            model_name='extra',
            name='to_tag',
            field=models.ForeignKey(to='tests.Extra', to_field=b'tag', null=True),
        ),
        migrations.AddField(
            model_name='brandt',
            name='labels',
            field=models.ManyToManyField(related_name='brands', through='tests.Labeling', to='tests.LabelT'),
        ),
        migrations.AddField(
            model_name='brand',
            name='labels',
            field=models.ManyToManyField(related_name='brands', to='tests.Label'),
        ),
        migrations.CreateModel(
            name='VideoProxy',
            fields=[
            ],
            options={
                'proxy': True,
            },
            bases=('tests.video',),
        ),
    ]

    from funcy import suppress

    with suppress(ImportError):
        import django.contrib.postgres.fields
        operations.append(
            migrations.CreateModel(
                name='TaggedPost',
                fields=[
                    ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                    ('name', models.CharField(max_length=200)),
                    ('tags', django.contrib.postgres.fields.ArrayField(base_field=models.IntegerField(), size=None)),
                ],
            )
        )

    if os.environ.get('CACHEOPS_DB') == 'postgis':
        import django.contrib.gis.db.models.fields
        operations.append(
            migrations.CreateModel(
                name='Geometry',
                fields=[
                    ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                    ('point', django.contrib.gis.db.models.fields.PointField(blank=True, default=None, dim=3, geography=True, null=True, srid=4326)),
                ],
            )
        )
