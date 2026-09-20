"""Bake independently compositable geometry for the interactive shedding demo."""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import asdict
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import study_shedding as study

from flower_cursor.idle_motion import IDLE_ANGULAR_SPEED, idle_pose
from flower_cursor.lifecycle import CursorCell, FlowerInstance
from flower_cursor.shedding import SheddingFlower

OUTPUT = Path(os.environ.get("FLOWER_LAYER_DIR", "/tmp/flower-layered-v1"))


def poses():
    flower = SheddingFlower(FlowerInstance.bud(1, CursorCell(0, 0)))
    for index in range(36):
        yield f"grow_{index:02d}", flower.pose
        flower.update(1 / 24)
    mature = FlowerInstance.bud(1, CursorCell(0, 0))
    mature.update(8)
    mature.idle_phase = 0
    for index in range(24):
        yield (
            f"idle_{index:02d}",
            idle_pose(mature.pose, index * math.tau / (24 * IDLE_ANGULAR_SPEED)),
        )
    for label, warmup in (("young", 0.25), ("half", 0.5), ("full", 3.0)):
        flower = SheddingFlower(FlowerInstance.bud(1, CursorCell(0, 0)))
        flower.update(warmup)
        flower.begin_withering()
        for index in range(25):
            yield f"exit_{label}_{index:02d}", flower.pose
            flower.update(1 / 24)


def group_name(obj):
    if obj.name.startswith("Petal_"):
        return f"petal_{int(obj.name.split('_')[1])}"
    if obj.name.startswith(("Sepal_", "Bud_", "Flower_Center", "Filament_", "Pollen_")):
        return "head"
    return "body"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    authored = study.authored
    authored.RENDER_DIR = OUTPUT
    authored.stem_point = study.stem_point
    selection = set(filter(None, os.environ.get("FLOWER_LAYER_POSES", "").split(",")))
    records = []
    for name, pose in poses():
        if selection and name not in selection:
            continue
        authored.POSES[name] = study.pose_channels(pose)
        scene = authored.build_scene(name)
        rot = authored.smoothstep((0.10 - pose.vitality) / 0.08)
        stem_rot = authored.smoothstep((0.07 - pose.vitality) / 0.05)
        leaf_rot = authored.smoothstep((0.24 - pose.vitality) / 0.21)
        browning = authored.smoothstep((0.22 - pose.vitality) / 0.195)
        objects = [o for o in scene.objects if o.type in {"MESH", "CURVE"}]
        origin = study.head_origin(pose)
        for obj in objects:
            if obj.type == "CURVE":
                obj.data.bevel_depth *= (1.0 - stem_rot) ** 1.2
            if group_name(obj) == "head":
                study.scale_mesh(obj, origin, 1.0 - rot)
            elif obj.name.startswith("Leaf_"):
                obj.scale *= 1.0 - leaf_rot
        for material_name in ("Living stem", "Leaf surface", "Sepal green"):
            mat = bpy.data.materials.get(material_name)
            if mat is not None:
                color = tuple(mat.diffuse_color)
                brown = (0.12, 0.075, 0.035)
                tinted = tuple(c * (1 - browning) + b * browning for c, b in zip(color[:3], brown))
                mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
                    *tinted,
                    color[3],
                )
        study.expand_canvas(scene)
        bpy.context.view_layer.update()
        head = world_to_camera_view(scene, scene.camera, origin)
        root = world_to_camera_view(scene, scene.camera, Vector())
        quaternion = scene.camera.rotation_euler.to_quaternion()
        right = quaternion @ Vector((1, 0, 0)) / scene.camera.data.ortho_scale
        down = quaternion @ Vector((0, -1, 0)) / scene.camera.data.ortho_scale
        record = {
            "name": name,
            "file": f"{name}_body.png",
            **authored.POSES[name],
            **asdict(pose),
            "collapse": pose.collapse,
            "petal_curl": pose.petal_curl,
            "retraction": pose.retraction,
            "head": [head.x, 1 - head.y],
            "world_head": list(origin),
            "layers": {},
            "centers": {},
            "depths": {},
        }
        for group in ("complete", "body", "head", *(f"petal_{i}" for i in range(6))):
            members = (
                objects if group == "complete" else [o for o in objects if group_name(o) == group]
            )
            vertices = [
                o.matrix_world @ v.co for o in members if o.type == "MESH" for v in o.data.vertices
            ]
            center = sum(vertices, Vector()) / len(vertices) if vertices else origin
            record["centers"][group] = list(center)
            record["depths"][group] = world_to_camera_view(scene, scene.camera, center).z
            path = OUTPUT / f"{name}_{group}.png"
            record["layers"][group] = path.name
            if not path.exists():
                for obj in objects:
                    obj.hide_render = obj not in members
                scene.render.filepath = str(path)
                bpy.ops.render.render(write_still=True)
        records.append(record)
        print(f"LAYERED {name}", flush=True)
    manifest = {
        "poses": records,
        "root_anchor": [root.x, 1 - root.y],
        "projection_right": list(right),
        "projection_down": list(down),
    }
    temporary = OUTPUT / "manifest.tmp"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n")
    temporary.replace(OUTPUT / "manifest.json")


if __name__ == "__main__":
    main()
