"""Build and render parameterized, translucent flower-cursor poses."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
BLEND_PATH = ROOT / "blender" / "flower_cursor.blend"


@dataclass(frozen=True, slots=True)
class FlowerStyle:
    name: str
    petal_colors: tuple[tuple[float, float, float, float], ...]
    center_color: tuple[float, float, float, float]
    filament_color: tuple[float, float, float, float]
    stem_color: tuple[float, float, float, float]
    leaf_color: tuple[float, float, float, float]
    petal_profile: str = "round"
    leaf_profile: str = "lance"
    petal_count: int = 8
    petal_length: float = 0.82
    petal_width: float = 0.34
    leaf_length: float = 0.78
    leaf_width: float = 0.34


STYLES = {
    "coral_anemone": FlowerStyle(
        name="coral_anemone",
        petal_colors=(
            (0.91, 0.30, 0.35, 0.80),
            (1.00, 0.52, 0.49, 0.74),
            (0.80, 0.14, 0.21, 0.84),
        ),
        center_color=(0.96, 0.42, 0.07, 1.0),
        filament_color=(1.00, 0.76, 0.18, 1.0),
        stem_color=(0.06, 0.26, 0.09, 1.0),
        leaf_color=(0.10, 0.42, 0.13, 0.94),
        petal_profile="round",
        leaf_profile="lance",
        petal_count=6,
        petal_length=0.73,
        petal_width=0.42,
        leaf_length=0.76,
        leaf_width=0.34,
    ),
    "violet_cosmos": FlowerStyle(
        name="violet_cosmos",
        petal_colors=(
            (0.42, 0.18, 0.86, 0.80),
            (0.68, 0.34, 1.00, 0.72),
            (0.28, 0.08, 0.64, 0.84),
        ),
        center_color=(0.95, 0.55, 0.08, 1.0),
        filament_color=(1.00, 0.82, 0.22, 1.0),
        stem_color=(0.06, 0.22, 0.12, 1.0),
        leaf_color=(0.12, 0.40, 0.18, 0.92),
        petal_profile="pointed",
        leaf_profile="fern",
        petal_count=9,
        petal_length=0.88,
        petal_width=0.28,
        leaf_length=0.86,
        leaf_width=0.27,
    ),
}

STYLE_NAME = os.environ.get("FLOWER_STYLE", "coral_anemone")
STYLE = STYLES.get(STYLE_NAME, STYLES["coral_anemone"])
RENDER_DIR = Path(os.environ.get("FLOWER_RENDER_DIR", str(ROOT / "assets" / "renders")))
MANIFEST_PATH = Path(
    os.environ.get("FLOWER_MANIFEST_PATH", str(ROOT / "assets" / "renders" / "manifest.json"))
)
SAVE_BLEND = os.environ.get("FLOWER_SAVE_BLEND", "1") != "0"


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def smoothstep(value: float) -> float:
    value = clamp01(value)
    return value * value * (3.0 - 2.0 * value)


def channels(
    stem: float,
    bloom: float,
    vitality: float,
    droop: float,
    *,
    idle_phase: float = 0.0,
    idle_lean: float = 0.0,
) -> dict[str, float]:
    leaf_unfurl = smoothstep((stem - 0.36) / 0.54) * (0.88 + 0.12 * vitality)
    bud_size = clamp01(0.18 + 0.78 * stem - 0.50 * bloom)
    return {
        "stem": stem,
        "bloom": bloom,
        "vitality": vitality,
        "droop": droop,
        "bud_size": bud_size,
        "leaf_unfurl": leaf_unfurl,
        "petal_open": bloom,
        "head_lean": idle_lean + droop * 0.35,
        "stem_bend": droop,
        "idle_phase": idle_phase,
        "idle_lean": idle_lean,
    }


POSES: dict[str, dict[str, float]] = {
    "bud": channels(0.25, 0.05, 1.0, 0.0),
    "bloom": channels(0.66, 0.55, 1.0, 0.0),
    "mature": channels(1.0, 1.0, 1.0, 0.0),
    "wilt": channels(0.25, 0.18, 0.35, 0.28),
}
for index in range(1, 24):
    amount = 0.05 + 0.95 * index / 24
    POSES[f"bloom_{index:02d}"] = channels(
        0.25 + 0.75 * amount,
        amount,
        1.0,
        0.0,
    )
for index in range(1, 13):
    amount = index / 12
    wither = channels(
        1.0 - 0.75 * amount,
        1.0 - 0.82 * amount,
        1.0 - 0.65 * amount,
        0.28 * amount,
    )
    late_wither = smoothstep((amount - 0.62) / 0.38)
    wither["stem_length"] = max(0.08, 1.0 - 0.75 * late_wither)
    wither["leaf_unfurl"] = max(0.0, 1.0 - 0.52 * amount)
    wither["stem_bend"] = 0.05 * amount + 0.62 * late_wither
    wither["head_lean"] = 0.02 * amount + 0.16 * late_wither
    POSES[f"wilt_{index:02d}"] = wither
for index in range(12):
    phase = 2 * math.pi * index / 12
    POSES[f"mature_idle_{index:02d}"] = channels(
        1.0,
        1.0,
        1.0,
        0.0,
        idle_phase=phase,
        idle_lean=0.10 * math.sin(phase),
    )


def write_manifest() -> None:
    entries = []
    for name, pose in POSES.items():
        entries.append(
            {
                "name": name,
                "stem": pose["stem"],
                "stem_length": pose.get("stem_length", pose["stem"]),
                "bloom": pose["bloom"],
                "vitality": pose["vitality"],
                "bud_size": pose["bud_size"],
                "leaf_unfurl": pose["leaf_unfurl"],
                "petal_open": pose["petal_open"],
                "head_lean": pose["head_lean"],
                "stem_bend": pose["stem_bend"],
                "file": f"flower_{name}.png",
                **({"idle_phase": pose["idle_phase"]} if "idle_phase" in pose else {}),
            }
        )
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(
            {
                "style": STYLE.name,
                "poses": entries,
            },
            indent=2,
        )
        + "\n"
    )


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
    name: str,
    color: tuple[float, float, float, float],
    roughness: float,
    *,
    transmission: float = 0.0,
    subsurface: float = 0.0,
) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    principled = mat.node_tree.nodes.get("Principled BSDF")
    assert principled is not None
    principled.inputs["Base Color"].default_value = color
    principled.inputs["Roughness"].default_value = roughness
    principled.inputs["IOR"].default_value = 1.38
    if "Alpha" in principled.inputs:
        principled.inputs["Alpha"].default_value = color[3]
    if "Transmission Weight" in principled.inputs:
        principled.inputs["Transmission Weight"].default_value = transmission
    if "Subsurface Weight" in principled.inputs:
        principled.inputs["Subsurface Weight"].default_value = subsurface
    if "Subsurface Radius" in principled.inputs:
        principled.inputs["Subsurface Radius"].default_value = (1.0, 0.35, 0.25)
    if "Coat Weight" in principled.inputs:
        principled.inputs["Coat Weight"].default_value = 0.28
    if "Coat Roughness" in principled.inputs:
        principled.inputs["Coat Roughness"].default_value = 0.16
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "DITHERED"
    elif hasattr(mat, "blend_method"):
        mat.blend_method = "BLEND"
    return mat


def add_cylinder_between(
    name: str,
    start: Vector,
    end: Vector,
    radius: float,
    mat: bpy.types.Material,
    *,
    vertices: int = 14,
) -> bpy.types.Object:
    direction = end - start
    midpoint = (start + end) / 2
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=vertices,
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
    return obj


def add_stem(
    mat: bpy.types.Material,
    stem_length: float,
    bend: float,
    head_lean: float,
) -> None:
    points = []
    for index in range(8):
        t = index / 7
        points.append(
            Vector(
                (
                    -0.035 * math.sin(t * math.pi) + head_lean * t**1.45 + bend * 0.65 * t**2.25,
                    0.025 * math.sin(t * math.pi * 1.4),
                    2.16 * stem_length * t,
                )
            )
        )
    curve_data = bpy.data.curves.new("Living stem curve", type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.resolution_u = 16
    curve_data.bevel_depth = 0.052
    curve_data.bevel_resolution = 4
    spline = curve_data.splines.new("BEZIER")
    spline.bezier_points.add(len(points) - 1)
    for index, (point, coordinate) in enumerate(zip(spline.bezier_points, points)):
        point.co = coordinate
        point.handle_left_type = "AUTO"
        point.handle_right_type = "AUTO"
        point.radius = 1.0 - 0.18 * index / (len(points) - 1)
    obj = bpy.data.objects.new("Living stem", curve_data)
    bpy.context.collection.objects.link(obj)
    curve_data.materials.append(mat)


def profile_widths(shape: str) -> tuple[float, ...]:
    if shape == "pointed":
        return (0.018, 0.10, 0.20, 0.27, 0.17, 0.07, 0.012)
    if shape == "fern":
        return (0.018, 0.12, 0.23, 0.30, 0.24, 0.12, 0.018)
    return (0.018, 0.16, 0.30, 0.40, 0.36, 0.26, 0.08)


def shaped_mesh(
    name: str,
    mat: bpy.types.Material,
    *,
    shape: str,
    length: float,
    width: float,
    curl: float,
    ridge: float,
) -> bpy.types.Object:
    row_positions = (0.0, 0.12, 0.28, 0.46, 0.64, 0.82, 1.0)
    widths = profile_widths(shape)
    verts = []
    for t, row_width in zip(row_positions, widths):
        for across in (-1.0, 0.0, 1.0):
            edge = abs(across)
            x = across * row_width * width
            y = -ridge * math.sin(math.pi * t) * (1.0 - edge**1.7) + 0.012 * edge
            z = length * t - curl * t**2 + ridge * 0.45 * math.sin(math.pi * t) * (1.0 - edge)
            verts.append((x, y, z))
    faces = []
    for row in range(len(row_positions) - 1):
        start = row * 3
        next_start = (row + 1) * 3
        for column in range(2):
            faces.append(
                (
                    start + column,
                    start + column + 1,
                    next_start + column + 1,
                    next_start + column,
                )
            )
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)
    solidify = obj.modifiers.new("Organic thickness", "SOLIDIFY")
    solidify.thickness = 0.026 if "Petal" in name else 0.018
    bevel = obj.modifiers.new("Soft edge", "BEVEL")
    bevel.width = 0.022 if "Petal" in name else 0.014
    bevel.segments = 2
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    return obj


def add_bud(
    mat: bpy.types.Material,
    sepal_mat: bpy.types.Material,
    center: Vector,
    bud_size: float,
    petal_open: float,
) -> None:
    if petal_open < 0.55:
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=24,
            ring_count=16,
            radius=1.0,
            location=center + Vector((0.0, 0.0, 0.10 * bud_size)),
        )
        bud = bpy.context.object
        bud.name = "Bud_Core"
        bud.scale = (0.15 + 0.07 * bud_size, 0.12 + 0.05 * bud_size, 0.18 + 0.11 * bud_size)
        bud.data.materials.append(mat)
        for polygon in bud.data.polygons:
            polygon.use_smooth = True

    sepal_amount = 1.0 - 0.55 * petal_open
    for index in range(3):
        angle = 2 * math.pi * index / 3 + 0.25
        sepal = shaped_mesh(
            f"Sepal_{index:02d}",
            sepal_mat,
            shape="pointed",
            length=0.32 * sepal_amount,
            width=0.18 * sepal_amount,
            curl=0.05,
            ridge=0.018,
        )
        sepal.location = center + Vector((0.0, 0.015, -0.07))
        sepal.rotation_euler[1] = angle
        sepal.rotation_euler[0] = -0.20 + 0.55 * petal_open


def add_petal_ring(
    petal_mats: tuple[bpy.types.Material, ...],
    bloom: float,
    droop: float,
    head_lean: float,
    flower_height: float,
) -> None:
    center = Vector((0.03 + head_lean, -0.045, flower_height))
    opening = clamp01(bloom)
    spread = 0.05 + 0.95 * opening
    petal_scale = 0.45 + 0.55 * opening
    head_pitch = 0.24 + 0.05 * opening
    for index in range(STYLE.petal_count):
        base_angle = 2 * math.pi * index / STYLE.petal_count + 0.42
        angle = base_angle * spread + 0.055 * math.sin(index * 2.7)
        radial = Vector((math.sin(angle), 0.0, math.cos(angle)))
        obj = shaped_mesh(
            f"Petal_{index:02d}",
            petal_mats[index % len(petal_mats)],
            shape=STYLE.petal_profile,
            length=STYLE.petal_length * petal_scale,
            width=STYLE.petal_width * petal_scale,
            curl=(0.07 + 0.13 * opening) + 0.16 * droop,
            ridge=0.052 + 0.025 * opening,
        )
        obj.location = center + radial * (0.03 + 0.09 * opening)
        obj.rotation_euler[1] = angle
        obj.rotation_euler[0] = head_pitch + droop * 0.65 * math.sin(base_angle)
        obj.rotation_euler[2] = 0.05 * math.sin(base_angle * 2.0)
        obj.scale = (
            0.96 + 0.07 * math.sin(index * 1.7),
            1.0,
            0.96 + 0.08 * math.cos(index * 1.3),
        )


def add_leaf(
    mat: bpy.types.Material,
    side: float,
    growth: float,
    droop: float,
    head_lean: float,
    stem_length: float,
    index: int,
) -> None:
    growth = clamp01(growth)
    if growth <= 0.01:
        return
    leaf = shaped_mesh(
        f"Leaf_{'L' if side < 0 else 'R'}_{index}",
        mat,
        shape=STYLE.leaf_profile,
        length=STYLE.leaf_length,
        width=STYLE.leaf_width,
        curl=0.12 + 0.12 * droop,
        ridge=0.034,
    )
    leaf.location = Vector(
        (
            side * (0.035 + 0.07 * growth) + head_lean * 0.42,
            -0.01,
            2.16 * stem_length * (0.53 + 0.10 * index),
        )
    )
    leaf.rotation_euler[1] = side * math.radians(68.0 - 22.0 * growth)
    leaf.rotation_euler[0] = math.radians(7.0) + droop * side * 0.65
    leaf.rotation_euler[2] = side * 0.08 * (1.0 - growth)
    leaf.scale = (growth, growth, growth)


def add_center(
    center_mat: bpy.types.Material,
    filament_mat: bpy.types.Material,
    petal_open: float,
    head_lean: float,
    flower_height: float,
) -> None:
    center = Vector((0.03 + head_lean, -0.18, flower_height + 0.01))
    radius = 0.15 * (0.84 + 0.16 * petal_open)
    bpy.ops.mesh.primitive_ico_sphere_add(
        subdivisions=3,
        radius=radius,
        location=center,
    )
    obj = bpy.context.object
    obj.name = "Flower_Center"
    obj.scale = (1.0, 0.62, 0.78)
    obj.data.materials.append(center_mat)
    for polygon in obj.data.polygons:
        polygon.use_smooth = True

    if petal_open < 0.28:
        return
    filament_amount = smoothstep((petal_open - 0.28) / 0.72)
    for index in range(10):
        angle = 2 * math.pi * index / 10 + 0.2
        direction = Vector((math.sin(angle), -0.02, math.cos(angle)))
        start = center + direction * radius * 0.45
        end = center + direction * radius * (0.90 + 0.22 * filament_amount)
        add_cylinder_between(
            f"Filament_{index:02d}",
            start,
            end,
            0.012,
            filament_mat,
            vertices=8,
        )
        bpy.ops.mesh.primitive_ico_sphere_add(
            subdivisions=2,
            radius=0.026,
            location=end,
        )
        pollen = bpy.context.object
        pollen.name = f"Pollen_{index:02d}"
        pollen.scale = (1.0, 0.75, 1.0)
        pollen.data.materials.append(filament_mat)


def add_camera() -> None:
    bpy.ops.object.camera_add(location=(2.65, -8.5, 1.35))
    camera = bpy.context.object
    camera.name = "Flower_Camera"
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 3.35
    direction = Vector((0.08, 0.0, 1.25)) - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = camera


def add_lights() -> None:
    for name, location, energy, size, color in (
        ("Key", (-3.5, -4.5, 6.0), 520.0, 4.0, (1.0, 0.72, 0.62)),
        ("Fill", (3.0, -2.0, 3.0), 300.0, 3.0, (0.52, 0.70, 1.0)),
        ("Rim", (0.0, 2.0, 4.5), 420.0, 2.5, (1.0, 0.42, 0.26)),
    ):
        bpy.ops.object.light_add(type="AREA", location=location)
        light = bpy.context.object
        light.name = f"Light_{name}"
        light.data.energy = energy
        light.data.shape = "DISK"
        light.data.size = size
        light.data.color = color
        direction = Vector((0.0, 0.0, 1.35)) - light.location
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
    scene.render.fps = 60
    try:
        scene.view_settings.look = "AgX - Medium High Contrast"
    except TypeError:
        pass
    return scene


def main(pose_name: str) -> None:
    if pose_name not in POSES:
        raise ValueError(f"unknown pose: {pose_name}")
    pose = POSES[pose_name]
    render_path = RENDER_DIR / f"flower_{pose_name}.png"
    render_path.parent.mkdir(parents=True, exist_ok=True)
    clear_scene()

    stem_length = pose.get("stem_length", pose["stem"])
    petal_open = pose["petal_open"]
    vitality = pose["vitality"]
    droop = pose["droop"]
    head_lean = pose["head_lean"]
    flower_height = 2.16 * stem_length

    petal_mats = tuple(
        material(
            f"{STYLE.name} petal {index}",
            color,
            0.22 + 0.10 * (1.0 - vitality),
            transmission=0.16,
            subsurface=0.075,
        )
        for index, color in enumerate(STYLE.petal_colors)
    )
    center_mat = material("Flower center", STYLE.center_color, 0.30, subsurface=0.04)
    filament_mat = material("Pollen gold", STYLE.filament_color, 0.24)
    stem_mat = material("Living stem", STYLE.stem_color, 0.40, subsurface=0.025)
    leaf_mat = material("Leaf surface", STYLE.leaf_color, 0.33, transmission=0.04, subsurface=0.05)
    sepal_mat = material("Sepal green", STYLE.leaf_color, 0.38, subsurface=0.04)

    add_stem(stem_mat, stem_length, pose["stem_bend"], head_lean)
    add_leaf(leaf_mat, -1.0, pose["leaf_unfurl"] * 1.05, droop, head_lean, stem_length, 0)
    add_leaf(leaf_mat, 1.0, (pose["leaf_unfurl"] - 0.12) / 0.88, droop, head_lean, stem_length, 1)
    add_petal_ring(petal_mats, petal_open, droop, head_lean, flower_height)
    add_bud(
        petal_mats[0],
        sepal_mat,
        Vector((0.03 + head_lean, -0.03, flower_height)),
        pose["bud_size"],
        petal_open,
    )
    add_center(center_mat, filament_mat, petal_open, head_lean, flower_height)
    add_camera()
    add_lights()
    scene = configure_render(render_path)
    if pose_name == "mature" and SAVE_BLEND:
        bpy.ops.wm.save_as_mainfile(filepath=str(BLEND_PATH), check_existing=False)
    write_manifest()
    bpy.ops.render.render(write_still=True)
    print(
        {
            "pose": pose_name,
            "style": STYLE.name,
            "render_path": str(render_path),
            "blend_path": str(BLEND_PATH),
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "engine": scene.render.engine,
            "transparent": scene.render.film_transparent,
            "objects": len(bpy.context.scene.objects),
        }
    )


main(globals().get("FLOWER_POSE", os.environ.get("FLOWER_POSE", "mature")))
