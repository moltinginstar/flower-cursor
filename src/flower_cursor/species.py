"""Species metadata shared by layered flower runtime components."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from .settings import DEFAULT_SPECIES


class ManifestError(ValueError):
  """A manifest whose contents do not describe a species."""


@dataclass(frozen=True, slots=True)
class SheddingGroup:
  identifier: str
  layer: str
  order: int


@dataclass(frozen=True, slots=True)
class SpeciesMetadata:
  species_id: str
  shedding_groups: tuple[SheddingGroup, ...]
  shedding_order: tuple[int, ...]
  landing_support_bounds: tuple[tuple[float, float], tuple[float, float]] | None = None


def metadata_from_manifest(data: Mapping[str, object], *, selected_species: str | None = None) -> SpeciesMetadata:
  species_id = data.get("species", DEFAULT_SPECIES)
  if not isinstance(species_id, str) or not species_id:
    raise ManifestError("manifest species must be a non-empty string")
  if selected_species is not None and species_id != selected_species:
    raise ManifestError(f"manifest species {species_id!r} does not match selected {selected_species!r}")

  raw_groups = data.get("shedding_groups")
  if not isinstance(raw_groups, list) or not raw_groups:
    raise ManifestError("manifest shedding_groups must be a non-empty list")
  groups = tuple(_parse_group(item, index) for index, item in enumerate(raw_groups))
  if len({group.identifier for group in groups}) != len(groups):
    raise ManifestError("manifest shedding_groups ids must be unique")
  if len({group.layer for group in groups}) != len(groups):
    raise ManifestError("manifest shedding_groups layers must be unique")
  raw_order = data.get("shedding_order")
  if raw_order is None:
    shedding_order = tuple(range(len(groups)))
  else:
    if not isinstance(raw_order, list):
      raise ManifestError("manifest shedding_order must be a list")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in raw_order):
      raise ManifestError("manifest shedding_order must contain integer indices")
    shedding_order = tuple(raw_order)
    if set(shedding_order) != set(range(len(groups))):
      raise ManifestError("manifest shedding_order must be a permutation of shedding groups")

  return SpeciesMetadata(
    species_id=species_id,
    shedding_groups=groups,
    shedding_order=shedding_order,
    landing_support_bounds=_parse_support_bounds(data.get("landing_support_bounds")),
  )


def _parse_group(value: object, index: int) -> SheddingGroup:
  if not isinstance(value, dict):
    raise ManifestError("manifest shedding_groups entries must be objects")
  identifier = value.get("id")
  layer = value.get("layer")
  order = value.get("order", index)
  if not isinstance(identifier, str) or not identifier:
    raise ManifestError("manifest shedding group id must be a non-empty string")
  if not isinstance(layer, str) or not layer:
    raise ManifestError("manifest shedding group layer must be a non-empty string")
  if isinstance(order, bool) or not isinstance(order, int) or order != index:
    raise ManifestError("manifest shedding group order must match declaration order")
  return SheddingGroup(identifier, layer, order)


def _parse_support_bounds(
  value: object,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
  if value is None:
    return None
  if not isinstance(value, dict):
    raise ManifestError("manifest landing_support_bounds must be an object")
  result = []
  for axis in ("x", "y"):
    bounds = value.get(axis)
    if not isinstance(bounds, list) or len(bounds) != 2:
      raise ManifestError(f"manifest landing_support_bounds.{axis} must contain two values")
    numbers = (float(bounds[0]), float(bounds[1]))
    if any(not math.isfinite(number) or abs(number) > 0.5 for number in numbers) or numbers[0] > numbers[1]:
      raise ManifestError(f"manifest landing_support_bounds.{axis} must be finite ordered bounds")
    result.append(numbers)
  return result[0], result[1]
