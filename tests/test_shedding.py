from copy import deepcopy

import pytest

from flower_cursor.lifecycle import CursorCell, FlowerInstance
from flower_cursor.shedding import SheddingFlower, petal_decay, petal_displacement


def mature_flower() -> SheddingFlower:
    flower = SheddingFlower(FlowerInstance.bud(1, CursorCell(4, 8)))
    flower.update(3.0)
    return flower


def test_petals_detach_in_staggered_order_and_capture_actual_pose() -> None:
    flower = mature_flower()
    flower.begin_withering()
    flower.update(0.13)
    assert [flight.index for flight in flower.flights] == [1]
    assert flower.petals[1].value == 0
    assert flower.flights[0].growth == 1
    assert 0 < flower.flights[0].pose.vitality < 1
    flower.update(0.27)
    assert [flight.index for flight in flower.flights] == [1, 4, 0, 5, 2, 3]
    assert all(petal.value == 0 for petal in flower.petals)


def test_restore_preserves_pose_and_velocities_without_recalling_fallen_petals() -> None:
    flower = mature_flower()
    flower.begin_withering()
    flower.update(0.30)
    before = flower.pose
    velocities = (flower._lean.velocity, flower._opening.velocity, flower.flower._stem.velocity)
    flights = [(f.identifier, f.age, f.pose) for f in flower.flights]
    growth = [p.value for p in flower.petals]
    flower.restore()
    assert flower.pose == before
    assert velocities == (
        flower._lean.velocity,
        flower._opening.velocity,
        flower.flower._stem.velocity,
    )
    assert [p.value for p in flower.petals] == growth
    flower.update(1 / 120)
    assert [(f.identifier, f.pose) for f in flower.flights] == [(i, pose) for i, _, pose in flights]
    for flight, (_, age, _) in zip(flower.flights, flights):
        assert flight.age == pytest.approx(age + 1 / 120)
    for previous, petal in zip(growth, flower.petals):
        assert previous <= petal.value <= previous + 0.01
    flower.update(1.0)
    assert flower.flights
    assert min(p.value for p in flower.petals) > 0.99
    flower.update(1.0)
    assert not flower.flights
    assert not flower.finished


def test_partial_growth_never_sheds_a_mature_flower() -> None:
    flower = SheddingFlower(FlowerInstance.bud(1, CursorCell(4, 8)))
    flower.update(0.2)
    original_stem = flower.pose.stem
    reference = deepcopy(flower.flower)
    reference.begin_withering()
    for _ in range(48):
        reference.update(1 / 120)
    flower.begin_withering()
    flower.update(0.4)
    assert flower.flights
    assert all(original_stem <= f.pose.stem <= reference.pose.stem + 1e-8 for f in flower.flights)
    assert all(f.pose.maturity < 0.01 for f in flower.flights)


def test_repeated_departures_create_new_flights_without_rewinding_old_ones() -> None:
    flower = mature_flower()
    flower.begin_withering()
    flower.update(0.2)
    old = {f.identifier: f.age for f in flower.flights}
    flower.restore()
    flower.update(0.1)
    flower.begin_withering()
    flower.update(0.2)
    assert len({f.identifier for f in flower.flights}) == len(flower.flights)
    assert any(f.identifier not in old for f in flower.flights)
    for flight in flower.flights:
        if flight.identifier in old:
            assert flight.age == pytest.approx(old[flight.identifier] + 0.3)


def test_exit_and_falling_debris_finish_without_a_lingering_remnant() -> None:
    flower = mature_flower()
    flower.begin_withering()
    flower.update(1.05)
    assert flower.flower.dead
    assert flower.flights
    assert not flower.finished
    assert all(p.value == 0 for p in flower.petals)
    flower.update(1.0)
    assert not flower.flights
    assert flower.finished


def test_detached_petals_decay_smoothly_before_flight_cleanup() -> None:
    assert petal_decay(0.0) == petal_decay(0.24) == 0.0
    assert 0.0 < petal_decay(0.8) < 1.0
    assert petal_decay(1.4) == petal_decay(1.6) == 1.0
    samples = [petal_decay(i / 100) for i in range(161)]
    assert samples == sorted(samples)
    assert petal_decay(0.24 + 1e-5) < 1e-8
    assert 1 - petal_decay(1.4 - 1e-5) < 1e-8


def test_scatter_preserves_release_position_and_velocity() -> None:
    velocity = (0.3, -0.2, -0.4)
    for index in range(6):
        assert petal_displacement(index, 0.0, velocity) == (0.0, 0.0, 0.0)
        step = 1e-7
        derivative = tuple(v / step for v in petal_displacement(index, step, velocity))
        assert derivative == pytest.approx(velocity, abs=3e-6)


def test_petals_scatter_both_ways_and_descend_more_slowly_than_stalk() -> None:
    points = [petal_displacement(i, 0.8, (0.0, 0.0, 0.0)) for i in range(6)]
    assert min(p[0] for p in points) < -0.8
    assert max(p[0] for p in points) > 0.6
    assert all(-1.0 < p[2] < 0.0 for p in points)
    assert all(petal_displacement(i, 1.6, (0.0, 0.0, 0.0))[2] < -3 for i in range(6))


@pytest.mark.parametrize("duration", [-1, float("inf"), float("nan")])
def test_invalid_time_does_not_mutate_the_study(duration) -> None:
    flower = mature_flower()
    before = flower.pose
    with pytest.raises(ValueError, match="finite and non-negative"):
        flower.update(duration)
    assert flower.pose == before
