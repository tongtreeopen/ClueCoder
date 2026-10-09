from datetime import date, timedelta


def date_range(start: date, end: date, step_days: int = 1) -> list[date]:
    """Return dates on or before end, stepping forward from start."""
    if step_days <= 0:
        raise ValueError('step_days must be positive')
    result = []
    current = start
    while current < end:
        result.append(current)
        current += timedelta(days=step_days)
    return result
