class BoundedQueue:
    def __init__(self, capacity):
        if not isinstance(capacity, int) or capacity <= 0:
            raise ValueError('capacity must be positive')
        self.capacity = capacity
        self.items = []

    def push(self, item):
        if len(self.items) < self.capacity:
            self.items.append(item)

    def pop(self):
        return self.items.pop(0)

    def __len__(self):
        return len(self.items)
