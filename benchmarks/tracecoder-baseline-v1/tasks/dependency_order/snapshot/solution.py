def dependency_order(graph: dict[str, list[str]]) -> list[str]:
    """Return dependencies before dependents; reject dependency cycles."""
    visited = set()
    result = []

    def visit(node):
        if node in visited:
            return
        visited.add(node)
        for dependency in graph.get(node, []):
            visit(dependency)
        result.append(node)

    for node in graph:
        visit(node)
    return result
