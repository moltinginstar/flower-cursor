"""Map continuous lifecycle state to authored flower imagery."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .lifecycle import FlowerPose


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


class PoseLibrary:
    def __init__(
        self, assets: tuple[PoseAsset, ...], *, root_anchor: tuple[float, float] = (0.5, 1.0)
    ) -> None:
        if not assets:
            raise ValueError("pose library must contain at least one asset")
        self._assets = assets
        self.root_anchor = root_anchor

    @classmethod
    def from_manifest(cls, manifest_path: Path) -> PoseLibrary:
        data = json.loads(manifest_path.read_text())
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
            )
            for item in data["poses"]
        )
        missing = [str(asset.path) for asset in assets if not asset.path.is_file()]
        if missing:
            raise FileNotFoundError(", ".join(missing))
        return cls(assets, root_anchor=tuple(data.get("root_anchor", (0.5, 1.0))))

    @classmethod
    def default(cls) -> PoseLibrary:
        manifest = Path(__file__).parents[2] / "assets" / "renders" / "manifest.json"
        return cls.from_manifest(manifest)

    @property
    def assets(self) -> tuple[PoseAsset, ...]:
        return self._assets

    def select(self, pose: FlowerPose) -> PoseAsset:
        return self._ranked(pose)[0][1]

    def sample(self, pose: FlowerPose) -> tuple[tuple[PoseAsset, float], ...]:
        neighbors = self._ranked(pose)[:3]
        if neighbors[0][0] < 1e-10 or len(neighbors) == 1:
            return ((neighbors[0][1], 1.0),)
        radius = neighbors[-1][0] * 1.001 + 1e-10
        weighted = [(asset, max(0.0, 1.0 - distance / radius) ** 2) for distance, asset in neighbors]
        total = sum(weight for _, weight in weighted)
        return tuple((asset, weight / total) for asset, weight in weighted if weight > 1e-8)

    def _ranked(self, pose: FlowerPose) -> list[tuple[float, PoseAsset]]:
        mature_factor = max(0.0, min(1.0, (pose.bloom - 0.95) / 0.05)) * max(
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

        def score(asset: PoseAsset) -> float:
            phase_distance = abs(
                (asset.idle_phase - pose.idle_phase + math.pi) % math.tau - math.pi
            )
            score_value = (
                (asset.stem - pose.stem) ** 2
                + (asset.bloom - pose.bloom) ** 2
                + 1.4 * (asset.vitality - pose.vitality) ** 2
                + mature_factor * 0.08 * phase_distance**2
            )
            if extended_state:
                score_value += (
                    0.30 * (asset.stem_length - pose.stem) ** 2
                    + 1.5 * (asset.maturity - pose.maturity) ** 2
                    + 0.25 * (asset.leaf_unfurl - pose.leaf_unfurl) ** 2
                    + 0.75 * (asset.petal_open - pose.petal_open) ** 2
                    + 0.5 * (asset.head_lean - pose.head_lean) ** 2
                    + 0.6 * (asset.stem_bend - pose.stem_bend) ** 2
                )
            return score_value

        return sorted(((score(asset), asset) for asset in self._assets), key=lambda item: item[0])
