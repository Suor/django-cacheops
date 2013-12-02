import six, hashlib

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
