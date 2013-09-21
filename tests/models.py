from django.db import models
from django.contrib.auth.models import User


# For basic tests and bench
class Category(models.Model):
    title = models.CharField(max_length=128)

    def __unicode__(self):
        return self.title

class Post(models.Model):
    title = models.CharField(max_length=128)
    category = models.ForeignKey(Category)
    visible = models.BooleanField(default=True)

    def __unicode__(self):
        return self.title

class Extra(models.Model):
    post = models.OneToOneField(Post)
    tag = models.IntegerField(db_column='custom_column_name', unique=True)
    to_tag = models.ForeignKey('self', to_field='tag', null=True)

    def __unicode__(self):
        return 'Extra(post_id=%s, tag=%s)' % (self.post_id, self.tag)

# 16
class Profile(models.Model):
    user = models.ForeignKey(User)
    tag = models.IntegerField()


# Proxy model
class Video(models.Model):
    title = models.CharField(max_length=128)

class VideoProxy(Video):
    class Meta:
        proxy = True


# Multi-table inheritance
class Media(models.Model):
    name = models.CharField(max_length=128)

class Movie(Media):
    year = models.IntegerField()


# Decimals
class Point(models.Model):
    x = models.DecimalField(decimal_places=6, max_digits=8, blank=True, default=0.0)



# 29
class Label(models.Model):
    text = models.CharField(max_length=127, blank=True, default='')

class MachineBrand(models.Model):
    labels = models.ManyToManyField(Label)


# local_get
class Local(models.Model):
    tag = models.IntegerField(null=True)


# 44
class Photo(models.Model):
    liked_user = models.ManyToManyField(User, through="PhotoLike")

class PhotoLike(models.Model):
    user = models.ForeignKey(User)
    photo = models.ForeignKey(Photo)
    timestamp = models.DateTimeField(auto_now_add=True)


# 45
class CacheOnSaveModel(models.Model):
    title = models.CharField(max_length=32)


# 47
class DbAgnostic(models.Model):
    pass

class DbBinded(models.Model):
    pass

