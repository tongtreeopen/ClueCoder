def balanced_brackets(text):
    pairs = {')': '(', ']': '[', '}': '{'}
    counts = {'(': 0, '[': 0, '{': 0}
    for char in text:
        if char in counts:
            counts[char] += 1
        elif char in pairs:
            counts[pairs[char]] -= 1
            if counts[pairs[char]] < 0:
                return False
    return all(value == 0 for value in counts.values())
