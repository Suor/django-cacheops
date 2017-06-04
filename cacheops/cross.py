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


# TODO: use django.utils.inspect.getargspec from Django 1.9
import inspect

if six.PY2:
    getargspec = inspect.getargspec
else:
    def getargspec(func):
        sig = inspect.signature(func)
        args = [
            p.name for p in sig.parameters.values()
            if p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
        ]
        varargs = [
            p.name for p in sig.parameters.values()
            if p.kind == inspect.Parameter.VAR_POSITIONAL
        ]
        varargs = varargs[0] if varargs else None
        varkw = [
            p.name for p in sig.parameters.values()
            if p.kind == inspect.Parameter.VAR_KEYWORD
        ]
        varkw = varkw[0] if varkw else None
        defaults = [
            p.default for p in sig.parameters.values()
            if p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD and p.default is not p.empty
        ] or None
        return args, varargs, varkw, defaults
