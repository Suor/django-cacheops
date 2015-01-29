import six, hashlib

# Use cPickle in python 2 and pickle in python 3
try:
    import cPickle as pickle
except ImportError:
    import pickle

# Adapt hashlib.md5 to eat str in python 3
if six.PY2:
    md5 = hashlib.md5
else:
    class md5:
        def __init__(self, s=None):
            self.md5 = hashlib.md5()
            if s is not None:
                self.update(s)

        def update(self, s):
            return self.md5.update(s.encode('utf-8'))

        def hexdigest(self):
            return self.md5.hexdigest()

def md5hex(s):
    return md5(s).hexdigest()
