"""Author the independent idle visitor; never loads or rewrites flower artwork."""

from __future__ import annotations

import json
import math
import os
import random
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(os.environ.get("BEE_OUTPUT", ROOT / "src/flower_cursor/assets/bee"))


def material(name, color, roughness=0.4, alpha=1.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    shader = mat.node_tree.nodes["Principled BSDF"]
    shader.inputs["Base Color"].default_value = (*color, 1)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Alpha"].default_value = alpha
    if alpha < 1:
        mat.surface_render_method = "BLENDED"
        shader.inputs["Coat Weight"].default_value = 0.4
        shader.inputs["IOR"].default_value = 1.38
    return mat


def ellipsoid(name, location, scale, mat):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=40, ring_count=24, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    obj.data.materials.append(mat)
    for face in obj.data.polygons:
        face.use_smooth = True
    return obj


def strand(name, points, radius, mat, parent=None):
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = radius
    curve.bevel_resolution = 2
    spline = curve.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for point, coordinate in zip(spline.points, points):
        point.co = (*coordinate, 1)
    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)
    obj.parent = parent
    return obj


def build():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    gold = material("Warm ochre pile", (0.56, 0.28, 0.035), 0.68)
    light = material("Fine honey hairs", (0.82, 0.49, 0.12), 0.75)
    black = material("Velvet charcoal", (0.022, 0.014, 0.009), 0.48)
    eye = material("Polished compound eyes", (0.012, 0.019, 0.023), 0.19)
    wing = material("Thin translucent chitin", (0.67, 0.80, 0.89), 0.24, 0.24)
    vein = material("Amber wing venation", (0.29, 0.22, 0.11), 0.5, 0.52)
    ellipsoid("Thorax", (0, 0, 0), (0.31, 0.25, 0.27), gold)
    abdomen = ellipsoid("Tapered striped abdomen", (0.44, 0, -0.045), (0.44, 0.26, 0.24), gold)
    abdomen.data.materials.append(black)
    for face in abdomen.data.polygons:
        x = sum(abdomen.data.vertices[i].co.x for i in face.vertices) / len(face.vertices)
        face.material_index = int((-0.7 < x < -0.43) or (-0.15 < x < 0.17) or x > 0.55)
    ellipsoid("Head", (-0.34, 0, -0.025), (0.215, 0.21, 0.215), black)
    for side in (-1, 1):
        ellipsoid("Compound eye", (-0.39, side * 0.159, 0.02), (0.13, 0.074, 0.16), eye)
        strand(
            "Jointed antenna",
            [(-0.44, side * 0.09, 0.12), (-0.58, side * 0.13, 0.3), (-0.72, side * 0.19, 0.27)],
            0.015,
            black,
        )
    rng = random.Random(43)
    for index in range(420):
        z = rng.uniform(-1, 1)
        angle = rng.uniform(0, math.tau)
        normal = Vector(
            (math.sqrt(1 - z * z) * math.cos(angle), math.sqrt(1 - z * z) * math.sin(angle), z)
        )
        base = Vector((normal.x * 0.31, normal.y * 0.25, normal.z * 0.27))
        tip = base + normal * rng.uniform(0.018, 0.047)
        strand(f"Thorax pile {index:03d}", [base, tip], 0.0024, light)
    wings = []
    for side in (-1, 1):
        for hind in (False, True):
            pivot = bpy.data.objects.new("Wing hinge", None)
            bpy.context.collection.objects.link(pivot)
            pivot.location = (0.05 + 0.15 * hind, side * 0.10, 0.17)
            length = 0.85 if not hind else 0.61
            outline = [(0, 0, 0)]
            for i in range(1, 33):
                t = i / 33
                outline.append(
                    (
                        0.36 * t + 0.19 * math.sin(math.pi * t),
                        side * length * math.sin(math.pi * t / 2),
                        0.025 * math.sin(math.pi * t),
                    )
                )
            for i in range(33, 0, -1):
                t = i / 33
                outline.append(
                    (
                        0.36 * t - 0.14 * math.sin(math.pi * t),
                        side * length * math.sin(math.pi * t / 2),
                        0,
                    )
                )
            mesh = bpy.data.meshes.new("Veined wing membrane")
            mesh.from_pydata(outline, [], [tuple(range(len(outline)))])
            mesh.update()
            obj = bpy.data.objects.new("Hindwing" if hind else "Forewing", mesh)
            bpy.context.collection.objects.link(obj)
            obj.parent = pivot
            obj.data.materials.append(wing)
            strand("Wing edge", outline + [outline[0]], 0.004, vein, pivot)
            for fraction in (0.28, 0.52, 0.78):
                strand(
                    "Radial vein",
                    [
                        (0, 0, 0.004),
                        (0.2 * fraction, side * length * fraction, 0.012),
                        (0.36 * fraction, side * length * math.sin(math.pi * fraction / 2), 0.015),
                    ],
                    0.003,
                    vein,
                    pivot,
                )
            wings.append((pivot, side, hind))
    legs = []
    for side in (-1, 1):
        for i in range(3):
            x = -0.16 + i * 0.18
            pivot = bpy.data.objects.new("Leg hip", None)
            bpy.context.collection.objects.link(pivot)
            pivot.location = (x, side * 0.16, -0.12)
            strand(
                "Articulated leg",
                [
                    (0, 0, 0),
                    (0.06, side * 0.16, -0.16),
                    (-0.06 + i * 0.07, side * 0.22, -0.32),
                    (-0.14 + i * 0.07, side * 0.24, -0.34),
                ],
                0.016,
                black,
                pivot,
            )
            legs.append((pivot, side))
    bpy.ops.object.camera_add(location=(2.8, -6.8, 3.0))
    camera = bpy.context.object
    camera.rotation_euler = (
        (Vector((0.05, 0, 0)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    )
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 2.45
    scene = bpy.context.scene
    scene.camera = camera
    for name, location, energy, size, color in (
        ("Key", (-3.5, -4.5, 6), 520, 4, (1, 0.72, 0.62)),
        ("Fill", (3, -2, 3), 300, 3, (0.52, 0.70, 1)),
        ("Rim", (0, 2, 4.5), 420, 2.5, (1, 0.42, 0.26)),
    ):
        bpy.ops.object.light_add(type="AREA", location=location)
        obj = bpy.context.object
        obj.name = name
        obj.data.energy, obj.data.size, obj.data.color = energy, size, color
        obj.rotation_euler = (-obj.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 24
    scene.cycles.use_denoising = True
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4
    scene.render.resolution_x = scene.render.resolution_y = 192
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.look = "AgX - Medium High Contrast"
    return scene, wings, legs


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    scene, wings, legs = build()
    records = []
    for i in range(16):
        scene.frame_set(i + 1)
        landing = max(0, (i - 7) / 8)
        phase = i * math.tau / 8
        for pivot, side, hind in wings:
            pivot.rotation_euler.x = side * (
                (1 - landing) * 0.65 * math.sin(phase + hind * 0.2) + landing * 0.12
            )
            pivot.rotation_euler.z = -side * landing * 1.08
        for pivot, side in legs:
            pivot.rotation_euler.x = side * (1 - landing) * 0.55
        for pivot, *_ in wings + legs:
            pivot.keyframe_insert(data_path="rotation_euler", frame=i + 1)
        name = f"bee_{i:02d}.png"
        scene.render.filepath = str(OUTPUT / name)
        bpy.ops.render.render(write_still=True)
        records.append(name)
    scene.frame_end = 16
    bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / "blender/idle_bee.blend"))
    (OUTPUT / "manifest.json").write_text(
        json.dumps(
            {
                "frames": records,
                "flight_frames": 8,
                "landing_frames": 9,
                "author": "Blender 5.2 / bake_idle_bee.py",
                "size": 192,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
