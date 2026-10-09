LEVELS = ('debug', 'info', 'warning', 'error', 'critical')


def filter_logs(records, minimum: str) -> list[dict]:
    """Keep records at or above minimum severity, preserving input order."""
    if minimum not in LEVELS:
        raise ValueError('Unknown minimum level')
    result = []
    for record in records:
        if record['level'] not in LEVELS:
            raise ValueError('Unknown record level')
        if record['level'] >= minimum:
            result.append(record)
    return result
