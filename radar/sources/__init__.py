"""Source adapters.

Each module exposes NAME, LABEL, list_ads() and fetch(ad). Record ids are
namespaced as "<source>:<id>" everywhere outside the adapter, because the three
sites number their adverts independently.
"""
from __future__ import annotations

from . import academic_positions, chalmers, kth

REGISTRY = {m.NAME: m for m in (academic_positions, kth, chalmers)}
DEFAULT = ["ap", "kth", "chalmers"]


def active(profile: dict):
    """The source modules this profile switches on, in profile order."""
    names = profile.get("sources") or DEFAULT
    if isinstance(names, dict):
        names = [n for n, on in names.items() if on]
    out = []
    for name in names:
        if name not in REGISTRY:
            raise KeyError(f"unknown source {name!r}; known: {sorted(REGISTRY)}")
        out.append(REGISTRY[name])
    return out


def key(source: str, ad_id: str) -> str:
    return f"{source}:{ad_id}"
