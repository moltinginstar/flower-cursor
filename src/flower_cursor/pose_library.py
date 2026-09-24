"""Map continuous lifecycle state to authored flower imagery."""

from __future__ import annotations

import heapq
import math
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .easing import clamp01
from .lifecycle import FlowerPose
from .manifest import manifest_data


@dataclass(frozen=True, slots=True)
class PoseAsset:
    name: str
    stem: float
    bloom: float
    vitality: float
    path: Path
    idle_phase: float = 0.0
    stem_length: float = 0.0
    leaf_unfurl: float = 0.0
    petal_open: float = 0.0
    head_lean: float = 0.0
    stem_bend: float = 0.0
    maturity: float = 0.0
    collapse: float = 0.0
    petal_curl: float = 0.0
    retraction: float = 0.0


class PoseLibrary:
    def __init__(
        self, assets: tuple[PoseAsset, ...], *, root_anchor: tuple[float, float] = (0.5, 1.0)
    ) -> None:
        if not assets:
            raise ValueError("pose library must contain at least one asset")
        self._assets = assets
        self.root_anchor = root_anchor
        idle = {
            asset.idle_phase % math.tau: asset
            for asset in assets
            if asset.name.startswith(("idle_", "mature_idle_"))
        }
        self._idle_phases = tuple(sorted(idle))
        self._idle_assets = tuple(idle[phase] for phase in self._idle_phases)

    @classmethod
    def from_manifest(cls, manifest_path: Path) -> PoseLibrary:
        # A library is immutable once built, and one runtime per cursor would
        # otherwise reparse the manifest for every cursor on screen.
        return _load_manifest(cls, manifest_path)

    @classmethod
    def _from_manifest_uncached(cls, manifest_path: Path) -> PoseLibrary:
        data = manifest_data(manifest_path)
        assets = tuple(
            PoseAsset(
                name=item["name"],
                stem=item.get("stem", item["bloom"]),
                bloom=item["bloom"],
                vitality=item["vitality"],
                path=manifest_path.parent / item["file"],
                idle_phase=item.get("idle_phase", 0.0),
                stem_length=item.get("stem_length", item.get("stem", 0.0)),
                leaf_unfurl=item.get("leaf_unfurl", 0.0),
                petal_open=item.get("petal_open", item.get("bloom", 0.0)),
                head_lean=item.get("head_lean", 0.0),
                stem_bend=item.get("stem_bend", 0.0),
                maturity=item.get("maturity", item.get("bloom", 0.0)),
                collapse=item.get("collapse", 0.0),
                petal_curl=item.get("petal_curl", 0.0),
                retraction=item.get("retraction", 0.0),
            )
            for item in data["poses"]
        )
        missing = [str(asset.path) for asset in assets if not asset.path.is_file()]
        if missing:
            raise FileNotFoundError(", ".join(missing))
        return cls(assets, root_anchor=tuple(data.get("root_anchor", (0.5, 1.0))))

    @property
    def assets(self) -> tuple[PoseAsset, ...]:
        return self._assets

    def select(self, pose: FlowerPose) -> PoseAsset:
        return self._ranked(pose, 1)[0][1]

    def sample(self, pose: FlowerPose) -> tuple[tuple[PoseAsset, float], ...]:
        neighbors = self._ranked(pose, 3)
        if neighbors[0][0] < 1e-10 or len(neighbors) == 1:
            return ((neighbors[0][1], 1.0),)
        radius = neighbors[-1][0] * 1.001 + 1e-10
        weighted = [
            (asset, max(0.0, 1.0 - distance / radius) ** 2) for distance, asset in neighbors
        ]
        total = sum(weight for _, weight in weighted)
        return tuple((asset, weight / total) for asset, weight in weighted if weight > 1e-8)

    def sample_animated(self, pose: FlowerPose) -> tuple[tuple[PoseAsset, float], ...]:
        """Blend into a phase-linear idle ring as the biological state settles."""
        progress = clamp01((min(pose.stem, pose.bloom, pose.vitality) - 0.95) / 0.045)
        if progress == 0 or len(self._idle_assets) < 2:
            return self.sample(pose)
        phase = pose.idle_phase % math.tau
        upper = bisect_right(self._idle_phases, phase) % len(self._idle_assets)
        lower = (upper - 1) % len(self._idle_assets)
        span = (self._idle_phases[upper] - self._idle_phases[lower]) % math.tau
        fraction = ((phase - self._idle_phases[lower]) % math.tau) / span
        idle = tuple(
            (self._idle_assets[index], weight)
            for index, weight in ((lower, 1 - fraction), (upper, fraction))
            if weight > 0
        )
        if progress == 1:
            return idle
        amount = progress * progress * (3 - 2 * progress)
        weights: dict[PoseAsset, float] = {}
        for samples, gain in ((self.sample(pose), 1 - amount), (idle, amount)):
            for asset, weight in samples:
                weights[asset] = weights.get(asset, 0.0) + weight * gain
        return tuple(weights.items())

    def _ranked(self, pose: FlowerPose, limit: int) -> list[tuple[float, PoseAsset]]:
        """The *limit* nearest assets, nearest first, ties in manifest order."""
        mature_factor = clamp01((pose.bloom - 0.95) / 0.05) * max(
            0.0, min(1.0, (pose.vitality - 0.95) / 0.05)
        )

        extended_state = any(
            (
                pose.bud_size,
                pose.leaf_unfurl,
                pose.petal_open,
                pose.head_lean,
                pose.stem_bend,
            )
        )
        # Read once: this runs against every asset several times a frame, and
        # the pose's collapse, curl and retraction are computed on each read.
        stem, bloom, vitality, idle_phase = pose.stem, pose.bloom, pose.vitality, pose.idle_phase
        maturity, leaf_unfurl, petal_open = pose.maturity, pose.leaf_unfurl, pose.petal_open
        head_lean, stem_bend = pose.head_lean, pose.stem_bend
        collapse, petal_curl, retraction = pose.collapse, pose.petal_curl, pose.retraction
        tau, pi = math.tau, math.pi

        def score(asset: PoseAsset) -> float:
            phase_distance = abs((asset.idle_phase - idle_phase + pi) % tau - pi)
            score_value = (
                (asset.stem - stem) ** 2
                + (asset.bloom - bloom) ** 2
                + 1.4 * (asset.vitality - vitality) ** 2
                + mature_factor * 0.08 * phase_distance**2
            )
            if extended_state:
                score_value += (
                    0.30 * (asset.stem_length - stem) ** 2
                    + 1.5 * (asset.maturity - maturity) ** 2
                    + 0.25 * (asset.leaf_unfurl - leaf_unfurl) ** 2
                    + 0.75 * (asset.petal_open - petal_open) ** 2
                    + 0.5 * (asset.head_lean - head_lean) ** 2
                    + 0.6 * (asset.stem_bend - stem_bend) ** 2
                    + (asset.collapse - collapse) ** 2
                    + 0.25 * (asset.petal_curl - petal_curl) ** 2
                    + 2.0 * (asset.retraction - retraction) ** 2
                )
            return score_value

        # nsmallest is sorted()[:limit], stable, without sorting every asset.
        return heapq.nsmallest(
            limit, ((score(asset), asset) for asset in self._assets), key=lambda item: item[0]
        )


@lru_cache(maxsize=8)
def _load_manifest(cls: type[PoseLibrary], manifest_path: Path) -> PoseLibrary:
    return cls._from_manifest_uncached(manifest_path)
