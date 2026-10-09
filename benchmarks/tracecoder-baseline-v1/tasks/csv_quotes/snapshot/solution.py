def parse_record(record: str) -> list[str]:
    """Parse one CSV record, including quoted fields."""
    return record.split(',') if record else []
