from django.db import models


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
    tag = models.IntegerField()

    def __unicode__(self):
        return 'Extra(post_id=%s, tag=%s)' % (self.post_id, self.tag)
