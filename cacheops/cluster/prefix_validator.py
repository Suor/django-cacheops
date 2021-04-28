import re


class InvalidPrefix(Exception):
    def __init__(self, prefix, message="prefix is not valid"):
        self.prefix = prefix
        self.message = f"{message} {prefix}"
        super().__init__(self.message)


def prefix_validator(prefix_fn):
    hash_tag_regex = r"\{.*\}"

    def wrapper(*args, **kwargs):
        prefix = prefix_fn(*args, **kwargs)
        if re.search(hash_tag_regex, prefix):
            raise InvalidPrefix(prefix)

        return prefix

    return wrapper
