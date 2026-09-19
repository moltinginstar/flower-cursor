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


class PoseLibrary:
    def __init__(self, assets: tuple[PoseAsset, ...]) -> None:
        if not assets:
            raise ValueError("pose library must contain at least one asset")
        self._assets = assets

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
            )
            for item in data["poses"]
        )
        missing = [str(asset.path) for asset in assets if not asset.path.is_file()]
        if missing:
            raise FileNotFoundError(", ".join(missing))
        return cls(assets)

    @classmethod
    def default(cls) -> PoseLibrary:
        manifest = Path(__file__).parents[2] / "assets" / "renders" / "manifest.json"
        return cls.from_manifest(manifest)

    @property
    def assets(self) -> tuple[PoseAsset, ...]:
        return self._assets

    def select(self, pose: FlowerPose) -> PoseAsset:
        mature_factor = max(0.0, min(1.0, (pose.bloom - 0.95) / 0.05)) * max(
            0.0, min(1.0, (pose.vitality - 0.95) / 0.05)
        )

        def score(asset: PoseAsset) -> tuple[float, str]:
            phase_distance = abs(
                (asset.idle_phase - pose.idle_phase + math.pi) % math.tau - math.pi
            )
            return (
                (asset.stem - pose.stem) ** 2
                + (asset.bloom - pose.bloom) ** 2
                + (asset.vitality - pose.vitality) ** 2
                + mature_factor * 0.08 * phase_distance**2,
                asset.name,
            )

        return min(self._assets, key=score)
