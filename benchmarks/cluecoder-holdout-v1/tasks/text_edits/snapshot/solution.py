def apply_edits(text, edits):
    previous_end = 0
    for start, end, replacement in edits:
        if start < previous_end or start < 0 or end < start or end > len(text):
            raise ValueError('invalid edit')
        text = text[:start] + replacement + text[end:]
        previous_end = end
    return text
