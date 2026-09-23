"""Evidence for inspecting directed paths and individual transfers."""
from collections import deque


def seed_paths(graph, seeds, max_hops=4):
    """One deterministic shortest path per seed and target, without cycles.

    Paths describe connectivity over the whole period, not a temporal money trace.
    A seed is not counted as reaching itself, matching the seed_reach metric.
    """
    result = {str(gid): [] for gid in graph}
    for seed in sorted(seeds):
        discovered = {seed: [seed]}
        queue = deque([seed])
        while queue:
            current = queue.popleft()
            path = discovered[current]
            if len(path) - 1 >= max_hops:
                continue
            for neighbor in sorted(graph.successors(current)):
                if neighbor in discovered:
                    continue
                route = path + [neighbor]
                discovered[neighbor] = route
                result[str(neighbor)].append([str(gid) for gid in route])
                queue.append(neighbor)
    for paths in result.values():
        paths.sort(key=lambda path: (len(path), tuple(map(int, path))))
    return result


def edge_details(edges, transactions):
    """Keep every observed operation, including duplicates on the same date."""
    grouped = {}
    ordered = transactions.sort_values(['src', 'dst', 'date', 'sum_kzt'], kind='stable')
    for row in ordered.itertuples(index=False):
        grouped.setdefault((int(row.src), int(row.dst)), []).append({
            'date': row.date.isoformat(), 'sum_kzt': float(row.sum_kzt),
        })
    result = []
    for row in edges.itertuples(index=False):
        operations = grouped[(int(row.src), int(row.dst))]
        result.append(dict(src=str(row.src), dst=str(row.dst), sum_kzt=float(row.sum_kzt),
                           n_tx=int(row.n_tx), depth=int(row.depth),
                           first_date=operations[0]['date'], last_date=operations[-1]['date'],
                           transactions=operations))
    return result
