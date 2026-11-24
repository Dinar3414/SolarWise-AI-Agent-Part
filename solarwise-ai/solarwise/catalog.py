"""Equipment catalogue loader (validated by Pydantic)."""
from __future__ import annotations
import json
from functools import lru_cache
from . import config
from .schemas import ModuleSpec, InverterSpec


@lru_cache(maxsize=1)
def _raw() -> dict:
    return json.loads(config.CATALOG_PATH.read_text(encoding="utf-8"))


def modules() -> list[ModuleSpec]:
    return [ModuleSpec(**m) for m in _raw()["modules"]]


def inverters() -> list[InverterSpec]:
    return [InverterSpec(**i) for i in _raw()["inverters"]]


def get_module(id_: str | None) -> ModuleSpec | None:
    return next((m for m in modules() if m.id == id_), None)


def get_inverter(id_: str | None) -> InverterSpec | None:
    return next((i for i in inverters() if i.id == id_), None)


def note() -> str:
    return _raw().get("_note", "")
