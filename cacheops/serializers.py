import pickle


class PickleSerializer:
    # properties
    PickleError = pickle.PickleError
    HIGHEST_PROTOCOL = pickle.HIGHEST_PROTOCOL

    # methods
    dumps = pickle.dumps
    loads = pickle.loads
