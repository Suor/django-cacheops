# 385
import dill


class DillSerializer:
    # properties
    PickleError = dill.PicklingError
    HIGHEST_PROTOCOL = dill.HIGHEST_PROTOCOL

    # methods
    dumps = dill.dumps
    loads = dill.loads
