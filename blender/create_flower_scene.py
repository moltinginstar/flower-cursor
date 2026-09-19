"""Build and render transparent flower-cursor poses."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
BLEND_PATH = ROOT / "blender" / "flower_cursor.blend"
POSES = {
    "bud": {"stem": 0.25, "bloom": 0.05, "vitality": 1.0, "droop": 0.0},
    "bloom": {"stem": 0.66, "bloom": 0.55, "vitality": 1.0, "droop": 0.0},
    "mature": {"stem": 1.0, "bloom": 1.0, "vitality": 1.0, "droop": 0.0},
    "wilt": {"stem": 0.25, "bloom": 0.18, "vitality": 0.35, "droop": 0.28},
}
for index in range(1, 24):
    amount = 0.05 + 0.95 * index / 24
    POSES[f"bloom_{index:02d}"] = {
        "stem": 0.25 + 0.75 * amount,
        "bloom": amount,
        "vitality": 1.0,
        "droop": 0.0,
    }
for index in range(1, 13):
    amount = index / 12
    POSES[f"wilt_{index:02d}"] = {
        "stem": 1.0 - 0.75 * amount,
        "bloom": 1.0 - 0.82 * amount,
        "vitality": 1.0 - 0.65 * amount,
        "droop": 0.28 * amount,
    }
for index in range(12):
    phase = 2 * math.pi * index / 12
    POSES[f"mature_idle_{index:02d}"] = {
        "stem": 1.0,
        "bloom": 1.0,
        "vitality": 1.0,
        "droop": 0.0,
        "idle_phase": phase,
        "idle_lean": 0.10 * math.sin(phase),
    }


def write_manifest() -> None:
    entries = []
    for name, pose in POSES.items():
        entry = {
            "name": name,
            "stem": pose.get("stem", pose["bloom"]),
            "bloom": pose["bloom"],
            "vitality": pose["vitality"],
            "file": f"flower_{name}.png",
        }
        if "idle_phase" in pose:
            entry["idle_phase"] = pose["idle_phase"]
        entries.append(entry)
    manifest_path = ROOT / "assets" / "renders" / "manifest.json"
    manifest_path.write_text(json.dumps({"poses": entries}, indent=2) + "\n")


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    datablocks_to_clean = (
        bpy.data.meshes,
        bpy.data.curves,
        bpy.data.materials,
        bpy.data.cameras,
        bpy.data.lights,
    )
    for datablocks in datablocks_to_clean:
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)


def material(
    name: str, color: tuple[float, float, float, float], roughness: float
) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    principled = mat.node_tree.nodes.get("Principled BSDF")
    assert principled is not None
    principled.inputs["Base Color"].default_value = color
    principled.inputs["Roughness"].default_value = roughness
    principled.inputs["IOR"].default_value = 1.38
    for input_name in ("Coat Weight", "Coat Roughness"):
        if input_name in principled.inputs:
            principled.inputs[input_name].default_value = (
                0.25 if input_name == "Coat Weight" else 0.18
            )
    if "Alpha" in principled.inputs:
        principled.inputs["Alpha"].default_value = color[3]
    if "Transmission Weight" in principled.inputs:
        principled.inputs["Transmission Weight"].default_value = 0.08
    return mat


def add_cylinder_between(
    name: str, start: Vector, end: Vector, radius: float, mat: bpy.types.Material
) -> None:
    direction = end - start
    midpoint = (start + end) / 2
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=16,
        radius=radius,
        depth=direction.length,
        location=midpoint,
    )
    obj = bpy.context.object
    obj.name = name
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = direction.to_track_quat("Z", "Y")
    obj.data.materials.append(mat)
    for polygon in obj.data.polygons:
        polygon.use_smooth = True


def add_stem(mat: bpy.types.Material, droop: float, idle_lean: float, stem_length: float) -> None:
    points = [
        Vector((0.0, 0.0, 0.0)),
        Vector((-0.04, 0.0, 0.65)),
        Vector((0.08, 0.0, 1.25)),
        Vector((0.0, 0.0, 1.85)),
        Vector((0.04 + droop * 0.35, 0.0, 2.15)),
    ]
    for point in points:
        normalized_height = point.z / 2.15
        point.z *= stem_length
        point.x += idle_lean * normalized_height**1.6
    for index, (start, end) in enumerate(zip(points, points[1:])):
        add_cylinder_between(f"Stem_{index:02d}", start, end, 0.055 - index * 0.005, mat)


def petal_mesh(name: str, mat: bpy.types.Material, scale: float = 1.0) -> bpy.types.Object:
    verts = [
        (0.0, 0.0, 0.0),
        (-0.22, 0.0, 0.22),
        (-0.31, 0.0, 0.62),
        (0.0, 0.0, 1.0),
        (0.31, 0.0, 0.62),
        (0.22, 0.0, 0.22),
        (0.0, -0.10, 0.46),
    ]
    faces = [(0, 1, 6), (1, 2, 6), (2, 3, 6), (3, 4, 6), (4, 5, 6), (5, 0, 6)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.scale = (scale, scale, scale)
    obj.data.materials.append(mat)
    solidify = obj.modifiers.new("Petal thickness", "SOLIDIFY")
    solidify.thickness = 0.035
    bevel = obj.modifiers.new("Petal softness", "BEVEL")
    bevel.width = 0.035
    bevel.segments = 2
    subdivision = obj.modifiers.new("Petal smoothness", "SUBSURF")
    subdivision.levels = 1
    subdivision.render_levels = 1
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    return obj


def add_petal_ring(
    mat: bpy.types.Material,
    bloom: float,
    droop: float,
    idle_lean: float,
    flower_height: float,
) -> None:
    center = Vector((0.02 + idle_lean, 0.0, flower_height))
    count = 7
    for index in range(count):
        base_angle = 2 * math.pi * index / count + 0.18
        angle = base_angle * (0.12 + 0.88 * bloom)
        radial = Vector((math.sin(angle), 0.0, math.cos(angle)))
        scale = 0.32 + 0.60 * bloom + 0.04 * (index % 2)
        obj = petal_mesh(f"Petal_{index:02d}", mat, scale=scale)
        obj.location = center + radial * (0.03 + 0.12 * bloom)
        obj.rotation_euler[1] = angle
        obj.rotation_euler[0] = 0.08 * math.sin(base_angle * 2) + droop * math.sin(base_angle)
        obj.rotation_euler[2] = droop * math.cos(base_angle) * 0.35


def add_leaf(
    mat: bpy.types.Material,
    side: float,
    droop: float,
    idle_lean: float,
    stem_length: float,
) -> None:
    leaf_growth = max(0.0, min(1.0, (stem_length - 0.22) / 0.78))
    obj = petal_mesh(f"Leaf_{'L' if side < 0 else 'R'}", mat, scale=0.82 * leaf_growth)
    obj.location = (
        0.02 * side + idle_lean * 0.48,
        -0.005,
        1.02 * stem_length,
    )
    obj.rotation_euler[1] = side * math.radians(58)
    obj.rotation_euler[0] = math.radians(5) + droop * side * 0.5
    obj.scale.y = 0.72


def add_center(
    mat: bpy.types.Material, bloom: float, idle_lean: float, flower_height: float
) -> None:
    radius = 0.30 * (0.72 + 0.28 * bloom)
    bpy.ops.mesh.primitive_ico_sphere_add(
        subdivisions=3,
        radius=radius,
        location=(0.02 + idle_lean, -0.08, flower_height),
    )
    obj = bpy.context.object
    obj.name = "Flower_Center"
    obj.scale = (1.0, 0.68, 0.86)
    obj.data.materials.append(mat)
    for polygon in obj.data.polygons:
        polygon.use_smooth = True


def add_camera() -> None:
    bpy.ops.object.camera_add(location=(0.0, -9.0, 2.0))
    camera = bpy.context.object
    camera.name = "Flower_Camera"
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 3.85
    direction = Vector((0.0, 0.0, 1.75)) - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = camera


def add_lights() -> None:
    for name, location, energy, size, color in (
        ("Key", (-3.5, -4.5, 6.0), 500.0, 4.0, (1.0, 0.75, 0.64)),
        ("Fill", (3.0, -2.0, 3.0), 260.0, 3.0, (0.56, 0.72, 1.0)),
        ("Rim", (0.0, 2.0, 4.5), 380.0, 2.5, (1.0, 0.45, 0.28)),
    ):
        bpy.ops.object.light_add(type="AREA", location=location)
        light = bpy.context.object
        light.name = f"Light_{name}"
        light.data.energy = energy
        light.data.shape = "DISK"
        light.data.size = size
        light.data.color = color
        direction = Vector((0.0, 0.0, 1.7)) - light.location
        light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def configure_render(render_path: Path) -> bpy.types.Scene:
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 256
    scene.render.resolution_y = 256
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.render.filepath = str(render_path)
    scene.render.use_file_extension = True
    scene.render.use_compositing = False
    scene.render.use_sequencer = False
    scene.render.image_settings.color_depth = "8"
    scene.render.fps = 24
    try:
        scene.view_settings.look = "AgX - Medium High Contrast"
    except TypeError:
        pass
    return scene


def main(pose_name: str) -> None:
    if pose_name not in POSES:
        raise ValueError(f"unknown pose: {pose_name}")
    pose = POSES[pose_name]
    render_path = ROOT / "assets" / "renders" / f"flower_{pose_name}.png"
    render_path.parent.mkdir(parents=True, exist_ok=True)
    clear_scene()
    stem_length = pose.get("stem", pose["bloom"])
    bloom = pose["bloom"]
    vitality = pose["vitality"]
    droop = pose["droop"]
    idle_lean = pose.get("idle_lean", 0.0)
    flower_height = 0.35 + 2.13 * stem_length
    petal_color = (
        0.78 + 0.14 * vitality,
        0.12 + 0.12 * vitality,
        0.16 + 0.12 * vitality,
        0.42 + 0.48 * vitality,
    )
    center_color = (0.62 + 0.38 * vitality, 0.27 + 0.28 * vitality, 0.04, 1.0)
    petal = material("Petal translucent coral", petal_color, 0.28 + 0.18 * (1 - vitality))
    stem = material("Stem living green", (0.08, 0.30, 0.10, 1.0), 0.42)
    leaf = material("Leaf green", (0.12, 0.48, 0.16, 0.96), 0.34)
    center = material("Golden center", center_color, 0.30)
    add_stem(stem, droop, idle_lean, stem_length)
    add_leaf(leaf, -1.0, droop, idle_lean, stem_length)
    add_leaf(leaf, 1.0, droop, idle_lean, stem_length)
    add_petal_ring(petal, bloom, droop, idle_lean, flower_height)
    add_center(center, bloom, idle_lean, flower_height)
    add_camera()
    add_lights()
    scene = configure_render(render_path)
    if pose_name == "mature":
        bpy.ops.wm.save_as_mainfile(filepath=str(BLEND_PATH), check_existing=False)
    write_manifest()
    bpy.ops.render.render(write_still=True)
    print(
        {
            "pose": pose_name,
            "render_path": str(render_path),
            "blend_path": str(BLEND_PATH),
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "engine": scene.render.engine,
            "transparent": scene.render.film_transparent,
            "objects": len(bpy.context.scene.objects),
        }
    )


main(globals().get("FLOWER_POSE", os.environ.get("FLOWER_POSE", "mature")))
