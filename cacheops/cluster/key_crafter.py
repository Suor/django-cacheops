def craft_scheme_key(prefix, table_name):
    return f"{prefix}schemes:{table_name}"

def craft_invalidator_key(prefix: str, db_table: str, conj: dict) -> str:
    """
    this function will generate the invalidator key
    from the invalidator key, we can invalidate old data

    invalidate_key -> data_key -> data

    the invalidator key will have the following format
    [prefix]conj:db_name:field1=value1&field2=value2

    this function is named `conj_cache_key` in lua script

    conj here is the dict contain field:value pair used by the filter query
    note that conj will not contain Q class query
    """
    parts = []
    for field, field_value in conj.items():
        if field == '':
            continue

        parts.append(f"{field}={field_value}")

    return f"{prefix}conj:{db_table}:{'&'.join(parts)}"
