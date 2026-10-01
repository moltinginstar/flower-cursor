"""`cycle` and `random`: a new species for every bloom, not one per window."""

import random
from itertools import pairwise

import pytest

from flower_cursor import check
from flower_cursor.drawing import LocalDrawing, Recipe
from flower_cursor.garden import Garden, load_species
from flower_cursor.geometry import ViewportGeometry
from flower_cursor.layered import GardenCompositor, LayeredCompositor, LayeredRuntime
from flower_cursor.lifecycle import CursorCell
from flower_cursor.multi_runtime import MultiCursorFlowers
from flower_cursor.settings import FLOWER_CATALOG, resolve

NAMES = [flower.name for flower in FLOWER_CATALOG]
FLOWERS = tuple((flower.manifest, flower.name) for flower in FLOWER_CATALOG)
SIZE = (80, 24, 640, 384)


def cells(count: int) -> list[CursorCell]:
  return [CursorCell(4, 4 + 6 * index) for index in range(count)]


def grown(runtime: LayeredRuntime, cell: CursorCell) -> str:
  """Move the caret to *cell* and say what budded there."""
  runtime.move_to(cell)
  runtime.update(1 / 60)
  return runtime.species_of(runtime.active).name


@pytest.mark.parametrize("word", ["cycle", "random"])
def test_the_two_words_offer_every_bundled_flower(word):
  settings = resolve({"species": word}, {})
  assert settings.order == word
  assert [name for _, name in settings.flowers] == NAMES
  assert [manifest for manifest, _ in settings.flowers] == [f.manifest for f in FLOWER_CATALOG]


def test_a_named_flower_is_still_one_flower_for_good():
  settings = resolve({"species": "poppy"}, {})
  assert settings.order == "fixed"
  assert [name for _, name in settings.flowers] == ["poppy"]


@pytest.mark.parametrize(
  ("word", "detail"),
  [
    ("cycle", "cycle, the next flower at every bloom (from --species)"),
    ("random", "random, another flower at every bloom (from --species)"),
  ],
)
def test_check_says_what_the_words_mean(word, detail):
  lines = check._setting_lines(resolve({"species": word}, {}))
  assert ("species", detail) in [(label, text) for _, label, text in lines]


def test_cycle_walks_the_catalog_in_order_from_wherever_it_starts():
  garden = Garden(FLOWERS, "cycle", rng=random.Random(3))
  picked = [garden.bloom().name for _ in range(2 * len(NAMES))]
  start = NAMES.index(picked[0])
  assert picked == [NAMES[(start + i) % len(NAMES)] for i in range(2 * len(NAMES))]


def test_cycle_does_not_start_every_window_on_the_same_flower():
  starts = {Garden(FLOWERS, "cycle", rng=random.Random(seed)).bloom().name for seed in range(40)}
  assert len(starts) > 1


def test_random_never_picks_the_same_flower_twice_running_and_reaches_them_all():
  garden = Garden(FLOWERS, "random", rng=random.Random(7))
  picked = [garden.bloom().name for _ in range(200)]
  assert all(a != b for a, b in pairwise(picked))
  assert set(picked) == set(NAMES)


def test_a_fixed_garden_only_ever_grows_its_one_flower():
  garden = Garden.single(FLOWERS[3][0], FLOWERS[3][1])
  assert {garden.bloom().name for _ in range(5)} == {NAMES[3]}
  with pytest.raises(ValueError, match="exactly one"):
    Garden(FLOWERS, "fixed")


def test_a_species_is_one_object_however_many_flowers_grow_it():
  # The compositor tells species apart by identity, and the shared renderer
  # relies on every window's cornflower being the same one.
  assert load_species(*FLOWERS[1]) is load_species(*FLOWERS[1])


def test_every_move_buds_the_next_species_and_each_flower_keeps_its_own():
  runtime = LayeredRuntime(FLOWERS[0][0], bee=False, garden=Garden(FLOWERS, "cycle", rng=random.Random(0)))
  a, b, c = cells(3)
  first, second, third = grown(runtime, a), grown(runtime, b), grown(runtime, c)
  assert NAMES.index(second) == (NAMES.index(first) + 1) % len(NAMES)
  assert NAMES.index(third) == (NAMES.index(second) + 1) % len(NAMES)
  # Every flower on screen (the two withering, the one opening) is drawn
  # as what it budded as, including how many petals it has to shed.
  by_cell = {visual.cell: visual for visual in runtime.visuals()}
  for cell, name in ((a, first), (b, second), (c, third)):
    species = by_cell[cell].species
    assert species.name == name
    assert len(by_cell[cell].flower.petals) == len(species.metadata.shedding_groups)
  # Going back to a flower that is still withering restores it as it was.
  assert grown(runtime, a) == first


def test_a_cleared_screen_starts_the_flowers_over_but_not_the_cycle():
  runtime = LayeredRuntime(FLOWERS[0][0], bee=False, garden=Garden(FLOWERS, "cycle", rng=random.Random(0)))
  a, b = cells(2)
  first = grown(runtime, a)
  runtime.reset()
  assert grown(runtime, b) == NAMES[(NAMES.index(first) + 1) % len(NAMES)]


def test_the_bee_rests_the_way_the_flower_under_it_lets_it():
  runtime = LayeredRuntime(FLOWERS[0][0], garden=Garden(FLOWERS, "cycle", rng=random.Random(0)))
  viewport = ViewportGeometry.measured(*SIZE)
  layout = runtime._layout
  for cell in cells(len(NAMES)):
    runtime.move_to(cell)
    runtime.set_geometry(viewport, layout)
    support = runtime.species_of(runtime.active).metadata.landing_support_bounds
    width, height = layout.size(viewport.cell_size)
    expected = (
      None
      if support is None
      else (
        (support[0][0] * width, support[0][1] * width),
        (support[1][0] * height, support[1][1] * height),
      )
    )
    assert runtime.bee.landing_support_bounds == expected
  # The flowers differ, so following the one under the caret is observable.
  assert len({load_species(*flower).metadata.landing_support_bounds for flower in FLOWERS}) > 2


def test_each_flower_is_drawn_with_its_own_species_artwork():
  garden = Garden(FLOWERS, "cycle", rng=random.Random(0))
  runtime = LayeredRuntime(FLOWERS[0][0], bee=False, garden=garden)
  compositor = GardenCompositor(garden.first)
  for cell in cells(3):
    runtime.move_to(cell)
    # Long enough to open past a bud, short of the first one withering away.
    for _ in range(10):
      runtime.update(1 / 60)
  shown = {visual.species for visual in runtime.visuals()}
  assert len(shown) == 3
  for visual in runtime.visuals():
    alone = LayeredCompositor(visual.species.manifest, species=visual.species.name)
    assert compositor.frame(visual).tobytes() == alone.frame(visual).tobytes()
  # Only what has been shown is held: the first flower's species, which is
  # also the one layout comes from, and the three that grew.
  assert set(compositor._compositors) <= shown | {garden.first}


def test_every_cursor_takes_its_turn_in_the_window_cycle():
  garden = Garden(FLOWERS, "cycle", rng=random.Random(0))
  primary = LayeredRuntime(FLOWERS[0][0], bee=False, garden=garden)
  extras = MultiCursorFlowers(FLOWERS[0][0], independent_limit=4, garden=garden)
  first = grown(primary, CursorCell(10, 10))
  extras.update(tuple(cells(2)), 1 / 60)
  names = [runtime.species_of(runtime.active).name for runtime in extras._runtimes]
  start = NAMES.index(first)
  assert names == [NAMES[(start + 1) % len(NAMES)], NAMES[(start + 2) % len(NAMES)]]


def test_a_cycling_recipe_crosses_the_socket_intact():
  recipe = Recipe.from_settings(resolve({"species": "cycle"}, {}), volume=0.0)
  assert recipe.order == "cycle"
  assert Recipe.from_json(recipe.to_json()) == recipe
  assert recipe.manifest == str(FLOWER_CATALOG[0].manifest.absolute())


def test_a_cycling_window_asks_the_renderer_for_each_species_it_draws():
  asked = []

  def images(manifest, name):
    asked.append(name)
    return {}

  recipe = Recipe.from_settings(resolve({"species": "cycle", "bee": "off"}, {}), volume=0.0)
  written = bytearray()
  drawing = LocalDrawing(recipe, written.extend, report=print, images=images)
  for cell in cells(3):
    drawing.move_to(cell)
    for _ in range(5):
      drawing.tick(1 / 60, cell, SIZE, True, ())
  shown = {v.species.name for v in drawing.runtime.visuals()}
  assert len(shown) == 3
  assert set(asked) == shown | {NAMES[0]}
  assert len(asked) == len(set(asked)), "a species was asked for twice"
  assert written
  drawing.close()
