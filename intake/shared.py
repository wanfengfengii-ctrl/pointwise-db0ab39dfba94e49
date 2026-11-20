"""Validation and storage types for shared-meter (共表) batch material."""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from intake.outlets import find_outlet
from intake.source_store import require_name


@dataclass(frozen=True)
class SharedSlot:
    """One ordered meter segment: a reading and the outlets it may feed."""

    ordinal: int
    ml: int
    outlets: tuple[str, ...]


@dataclass(frozen=True)
class OutletBound:
    """Closed daily bound for one outlet."""

    outlet: str
    min_ml: int
    max_ml: int


def _sequence(value, field):
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f'{field} must be an ordered sequence')
    return value


def _integer(value, field, minimum=None):
    if type(value) is not int:
        raise ValueError(f'{field} must be an integer')
    if minimum is not None and value < minimum:
        raise ValueError(f'{field} must be at least {minimum}')
    return value


def validate_slots(db, plant, slots):
    _sequence(slots, 'slots')
    validated = []
    for ordinal, item in enumerate(slots, start=1):
        if not isinstance(item, Mapping) or set(item) != {'ml', 'outlets'}:
            raise ValueError(f'slot {ordinal} requires ml and outlets')
        ml = _integer(item['ml'], f'slot {ordinal} ml', 0)
        outlets = _sequence(item['outlets'], f'slot {ordinal} outlets')
        seen = set()
        names = []
        for name in outlets:
            name = require_name(name, f'slot {ordinal} outlet')
            if name in seen:
                raise ValueError(f'slot {ordinal} repeats outlet {name}')
            seen.add(name)
            names.append(name)
        validated.append(SharedSlot(ordinal, ml, tuple(sorted(names))))
    return tuple(validated)


def validate_bounds(db, plant, slots, bounds):
    if not isinstance(bounds, Mapping):
        raise ValueError('bounds must be a mapping')
    validated = []
    seen = set()
    for outlet, limits in bounds.items():
        outlet = require_name(outlet, 'bound outlet')
        if outlet in seen:
            raise ValueError(f'bound outlet {outlet} is repeated')
        seen.add(outlet)
        if not isinstance(limits, Mapping) or set(limits) != {'min_ml', 'max_ml'}:
            raise ValueError(f'bound {outlet} requires min_ml and max_ml')
        minimum = _integer(limits['min_ml'], f'bound {outlet} min_ml', 0)
        maximum = _integer(limits['max_ml'], f'bound {outlet} max_ml', 0)
        if maximum < minimum:
            raise ValueError(f'bound {outlet} max_ml is below min_ml')
        if not find_outlet(db, plant, outlet):
            raise ValueError(f'bound outlet {outlet} is not registered in {plant}')
        validated.append(OutletBound(outlet, minimum, maximum))
    permitted = {outlet for slot in slots for outlet in slot.outlets}
    missing = permitted - seen
    if missing:
        raise ValueError(f'slot outlet {sorted(missing)[0]} has no daily bound')
    return tuple(sorted(validated, key=lambda bound: bound.outlet))
