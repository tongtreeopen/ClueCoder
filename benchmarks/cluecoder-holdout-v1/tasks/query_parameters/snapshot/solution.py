def parse_query(query):
    result = {}
    for part in query.split('&'):
        if not part:
            continue
        name, _, value = part.partition('=')
        result.setdefault(name, []).append(value)
    return result
