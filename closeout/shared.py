"""Feasible integer allocation for shared-meter (共表) revisions.

A shared day has ordered slots; each slot's reading must be split across the
outlets that slot permits.  Every outlet has a closed daily bound.  The split is
a bipartite transportation feasibility problem, solved here as a circulation
with lower bounds so that both the per-slot conservation and the per-outlet
daily bounds are honoured exactly, in whole millilitres.
"""
from dataclasses import dataclass

from closeout.totals import calculate_totals
from intake.revisions import RevisionRow


@dataclass(frozen=True)
class AllocatedSlot:
    ordinal: int
    ml: int
    outlets: tuple[str, ...]
    allocation: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class SharedCloseout:
    slots: tuple[AllocatedSlot, ...]
    bounds: tuple[tuple[str, int, int], ...]

    @property
    def rows(self):
        """Formal ledger rows: slot position then outlet name, positives only."""
        rows = []
        ordinal = 0
        for slot in self.slots:
            for outlet, ml in slot.allocation:
                if ml <= 0:
                    continue
                ordinal += 1
                rows.append(RevisionRow(ordinal, outlet, ml))
        return tuple(rows)

    @property
    def totals(self):
        groups = {}
        for row in self.rows:
            groups.setdefault(row.outlet, []).append(row)
        return calculate_totals(tuple((outlet, tuple(groups[outlet]))
                                      for outlet in sorted(groups)))


class _Edge:
    __slots__ = ('to', 'reverse', 'capacity', 'initial')

    def __init__(self, to, reverse, capacity):
        self.to = to
        self.reverse = reverse
        self.capacity = capacity
        self.initial = capacity


class _Network:
    """Small Dinic max-flow over Python integers (arbitrary precision)."""

    def __init__(self, size):
        self.graph = [[] for _ in range(size)]

    def add(self, source, target, capacity):
        forward = _Edge(target, len(self.graph[target]), capacity)
        backward = _Edge(source, len(self.graph[source]), 0)
        self.graph[source].append(forward)
        self.graph[target].append(backward)
        return forward

    def _levels(self, source, target):
        level = [-1] * len(self.graph)
        level[source] = 0
        queue = [source]
        for node in queue:
            for edge in self.graph[node]:
                if edge.capacity > 0 and level[edge.to] < 0:
                    level[edge.to] = level[node] + 1
                    queue.append(edge.to)
        return level if level[target] >= 0 else None

    def _augment(self, node, target, pushed, level, cursor):
        if node == target:
            return pushed
        while cursor[node] < len(self.graph[node]):
            edge = self.graph[node][cursor[node]]
            if edge.capacity > 0 and level[edge.to] == level[node] + 1:
                amount = self._augment(edge.to, target, min(pushed, edge.capacity),
                                       level, cursor)
                if amount:
                    edge.capacity -= amount
                    self.graph[edge.to][edge.reverse].capacity += amount
                    return amount
            cursor[node] += 1
        return 0

    def max_flow(self, source, target, limit):
        total = 0
        while total < limit:
            level = self._levels(source, target)
            if level is None:
                break
            cursor = [0] * len(self.graph)
            while total < limit:
                amount = self._augment(source, target, limit - total, level, cursor)
                if not amount:
                    break
                total += amount
        return total


def _infeasible():
    return ValueError('shared readings have no allocation within the daily bounds')


def solve_allocation(slots, bounds):
    """Choose one feasible whole-mL split and freeze it as a SharedCloseout."""
    limits = {bound.outlet: (bound.min_ml, bound.max_ml) for bound in bounds}
    count = len(slots)
    outlet_names = sorted({outlet for slot in slots for outlet in slot.outlets} | set(limits))
    outlet_index = {outlet: index for index, outlet in enumerate(outlet_names)}
    total_ml = sum(slot.ml for slot in slots)

    source = 0
    slot_base = 1
    outlet_base = slot_base + count
    sink = outlet_base + len(outlet_names)
    nodes = sink + 1

    # Build the lower-bound circulation edge list.
    # Each entry: (start, end, low, high, tag) where tag is None for real
    # allocation edges or ('edge', start, end) for helper edges we ignore.
    edge_specs = []

    def add_edge(start, end, low, high, tag):
        if low > high or low < 0:
            raise _infeasible()
        edge_specs.append((start, end, low, high, tag))

    for position, slot in enumerate(slots):
        start = slot_base + position
        add_edge(source, start, slot.ml, slot.ml, None)
        for outlet in slot.outlets:
            add_edge(start, outlet_base + outlet_index[outlet], 0, slot.ml,
                     ('allocation', start, outlet_base + outlet_index[outlet]))

    for outlet in outlet_names:
        minimum, maximum = limits.get(outlet, (0, 0))
        add_edge(outlet_base + outlet_index[outlet], sink, minimum, maximum, None)

    add_edge(sink, source, 0, total_ml, None)

    balance = [0] * nodes
    for start, end, low, _high, _tag in edge_specs:
        balance[start] -= low
        balance[end] += low

    super_source = nodes
    super_sink = nodes + 1
    flow = _Network(nodes + 2)
    allocation_edges = {}
    for start, end, low, high, tag in edge_specs:
        edge = flow.add(start, end, high - low)
        if tag is not None:
            allocation_edges[tag] = (low, edge)

    needed = 0
    for node in range(nodes):
        if balance[node] > 0:
            flow.add(super_source, node, balance[node])
            needed += balance[node]
        elif balance[node] < 0:
            flow.add(node, super_sink, -balance[node])

    if flow.max_flow(super_source, super_sink, needed) != needed:
        raise _infeasible()

    allocated = []
    for position, slot in enumerate(slots):
        start = slot_base + position
        parts = []
        for outlet in slot.outlets:
            tag = ('allocation', start, outlet_base + outlet_index[outlet])
            low, edge = allocation_edges[tag]
            amount = low + (edge.initial - edge.capacity)
            if amount > 0:
                parts.append((outlet, amount))
        parts.sort(key=lambda item: item[0])
        if sum(amount for _, amount in parts) != slot.ml:
            raise _infeasible()
        allocated.append(AllocatedSlot(slot.ordinal, slot.ml, slot.outlets, tuple(parts)))

    for outlet in outlet_names:
        amount = sum(part for slot in allocated
                     for part_outlet, part in slot.allocation if part_outlet == outlet)
        minimum, maximum = limits.get(outlet, (0, 0))
        if not minimum <= amount <= maximum:
            raise _infeasible()

    return SharedCloseout(tuple(allocated), tuple(
        (outlet, limits[outlet][0], limits[outlet][1]) for outlet in outlet_names
    ))
