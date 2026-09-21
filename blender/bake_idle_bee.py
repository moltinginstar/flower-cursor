"""Author the independent idle visitor; never loads or rewrites flower artwork."""

from __future__ import annotations

import json
import math
import os
import random
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(os.environ.get("BEE_OUTPUT", ROOT / "src/flower_cursor/assets/bee"))
HEADINGS = (-180, -135, -90, -45, -25, 0, 25, 45, 90, 135)


def material(name, color, roughness=0.4, alpha=1.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    shader = mat.node_tree.nodes["Principled BSDF"]
    shader.inputs["Base Color"].default_value = (*color, 1)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Alpha"].default_value = alpha
    if alpha < 1:
        mat.surface_render_method = "BLENDED"
        shader.inputs["Coat Weight"].default_value = 0.3
        shader.inputs["IOR"].default_value = 1.38
    return mat


def organic_surface(mat, low, high, scale=9, relief=0.002):
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = scale
    noise.inputs["Detail"].default_value = 3
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*low, 1)
    ramp.color_ramp.elements[1].color = (*high, 1)
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    shader = nodes["Principled BSDF"]
    links.new(ramp.outputs["Color"], shader.inputs["Base Color"])
    grain = nodes.new("ShaderNodeTexNoise")
    grain.inputs["Scale"].default_value = 180
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.18
    bump.inputs["Distance"].default_value = relief
    links.new(grain.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], shader.inputs["Normal"])


def fur(name, fibers, mat):
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = 0.00075
    curve.bevel_resolution = 1
    for base, middle, tip in fibers:
        spline = curve.splines.new("POLY")
        spline.points.add(2)
        for point, coordinate, radius in zip(spline.points, (base, middle, tip), (1, 0.6, 0.05)):
            point.co = (*coordinate, 1)
            point.radius = radius
    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj


def ellipsoid(name, location, scale, mat):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=40, ring_count=24, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    obj.data.materials.append(mat)
    for face in obj.data.polygons:
        face.use_smooth = True
    return obj


def strand(name, points, radius, mat, parent=None, taper=1.0):
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = radius
    curve.bevel_resolution = 2
    spline = curve.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for index, (point, coordinate) in enumerate(zip(spline.points, points)):
        point.co = (*coordinate, 1)
        point.radius = 1 + (taper - 1) * index / max(1, len(points) - 1)
    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)
    obj.parent = parent
    return obj


def abdomen(gold, dark, hair):
    vertices, faces = [], []
    rings, sides = 100, 48
    for i in range(rings + 1):
        t = i / rings
        radius = 0.255 * math.sin(math.pi * t) ** 0.65 * (1.12 - 0.48 * t)
        groove = 1 - 0.045 * math.exp(-(((t * 5 % 1 - 0.02) / 0.06) ** 2))
        for j in range(sides):
            a = j * math.tau / sides
            vertices.append(
                (
                    0.08 + 0.66 * t,
                    radius * groove * math.cos(a),
                    -0.05 - 0.13 * t * t + radius * groove * 0.87 * math.sin(a),
                )
            )
    for i in range(rings):
        for j in range(sides):
            a = i * sides + j
            b = i * sides + (j + 1) % sides
            faces.append((a, b, b + sides, a + sides))
    mesh = bpy.data.meshes.new("Curved tapered abdominal tergites")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("Segmented abdomen", mesh)
    bpy.context.collection.objects.link(obj)
    for mat in (gold, dark):
        mesh.materials.append(mat)
    for face in mesh.polygons:
        t = (face.index // sides + 0.5) / rings
        face.material_index = int(0.25 < t < 0.36 or 0.49 < t < 0.60 or t > 0.75)
        face.use_smooth = True
    rng = random.Random(71)
    fibers = []
    for i in range(2100):
        t = rng.uniform(0.05, 0.9)
        a = rng.uniform(0, math.tau)
        radius = 0.255 * math.sin(math.pi * t) ** 0.65 * (1.12 - 0.48 * t)
        base = Vector(
            (
                0.08 + 0.66 * t,
                radius * math.cos(a),
                -0.05 - 0.13 * t * t + radius * 0.87 * math.sin(a),
            )
        )
        normal = Vector((0.32, math.cos(a), math.sin(a)))
        length = rng.uniform(0.008, 0.019)
        fibers.append(
            (base, base + normal * length * 0.6, base + normal * length + Vector((0.009, 0, 0)))
        )
    fur("Fine abdominal nap", fibers, hair)


def build():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    gold = material("Satin honey amber tergites", (0.27, 0.16, 0.052), 0.51)
    organic_surface(gold, (0.15, 0.070, 0.020), (0.39, 0.24, 0.088), 7)
    gold.node_tree.nodes["Principled BSDF"].inputs["Subsurface Weight"].default_value = 0.035
    pile = material("Umber thorax underfur", (0.095, 0.064, 0.032), 0.9)
    organic_surface(pile, (0.055, 0.033, 0.014), (0.19, 0.13, 0.064), 12)
    light = material("Silken champagne guard hairs", (0.46, 0.34, 0.18), 0.72)
    light.node_tree.nodes["Principled BSDF"].inputs["Sheen Weight"].default_value = 0.28
    black = material("Soft umber chitin", (0.030, 0.021, 0.013), 0.53)
    organic_surface(black, (0.013, 0.009, 0.006), (0.054, 0.036, 0.019), 12)
    eye = material("Smoky compound eyes", (0.011, 0.015, 0.016), 0.32)
    wing = material("Pearlescent wing membrane", (0.70, 0.74, 0.71), 0.3, 0.14)
    wing.node_tree.nodes["Principled BSDF"].inputs["Transmission Weight"].default_value = 0.25
    vein = material("Fine sepia wing veins", (0.20, 0.16, 0.095), 0.62, 0.28)
    ellipsoid("Rounded thorax", (-0.075, 0, 0.025), (0.265, 0.235, 0.285), pile)
    abdomen(gold, black, light)
    ellipsoid("Distinct head", (-0.38, 0, -0.015), (0.155, 0.169, 0.18), black)
    for side in (-1, 1):
        ellipsoid("Compound eye", (-0.426, side * 0.143, 0.015), (0.082, 0.044, 0.117), eye)
        strand(
            "Elbowed antenna",
            [(-0.48, side * 0.07, 0.105), (-0.58, side * 0.12, 0.235), (-0.73, side * 0.15, 0.22)],
            0.010,
            black,
        )
        ellipsoid("Antenna tip", (-0.73, side * 0.15, 0.22), (0.015, 0.011, 0.012), black)
    strand("Mouthparts", [(-0.52, -0.02, -0.10), (-0.56, -0.02, -0.20)], 0.015, black)
    rng = random.Random(43)
    fibers = []
    for index in range(7000):
        z = rng.uniform(-0.7, 1)
        angle = rng.uniform(0, math.tau)
        normal = Vector(
            (math.sqrt(1 - z * z) * math.cos(angle), math.sqrt(1 - z * z) * math.sin(angle), z)
        )
        base = Vector((-0.075 + normal.x * 0.265, normal.y * 0.235, 0.025 + normal.z * 0.285))
        length = rng.uniform(0.016, 0.033)
        sweep = Vector((0.014, normal.y * 0.006, -0.008))
        fibers.append((base, base + normal * length * 0.65, base + normal * length + sweep))
    fur("Swept thorax velvet", fibers, light)
    fibers = []
    for index in range(850):
        z = rng.uniform(-0.8, 1)
        angle = rng.uniform(0, math.tau)
        normal = Vector(
            (math.sqrt(1 - z * z) * math.cos(angle), math.sqrt(1 - z * z) * math.sin(angle), z)
        )
        if abs(normal.y) > 0.65:
            continue
        base = Vector((-0.38 + normal.x * 0.155, normal.y * 0.169, -0.015 + normal.z * 0.18))
        fibers.append(
            (base, base + normal * 0.008, base + normal * 0.013 + Vector((0.003, 0, -0.003)))
        )
    fur("Fine facial pile", fibers, light)
    wings = []
    for side in (-1, 1):
        for hind in (False, True):
            pivot = bpy.data.objects.new("Wing hinge", None)
            bpy.context.collection.objects.link(pivot)
            pivot.location = (-0.015 + 0.12 * hind, side * 0.11, 0.18)
            length = 0.79 if not hind else 0.52
            outline = [(0, 0, 0)]
            for edge in (1, -1):
                steps = range(1, 33) if edge == 1 else range(32, 0, -1)
                for i in steps:
                    t = i / 32
                    outline.append(
                        (
                            0.40 * t + edge * 0.13 * math.sin(math.pi * t) ** 0.7,
                            side * length * t,
                            0.018 * math.sin(math.pi * t),
                        )
                    )
            mesh = bpy.data.meshes.new("Slender wing membrane")
            mesh.from_pydata(outline, [], [tuple(range(len(outline)))])
            mesh.update()
            obj = bpy.data.objects.new("Hindwing" if hind else "Forewing", mesh)
            bpy.context.collection.objects.link(obj)
            obj.parent = pivot
            obj.data.materials.append(wing)
            strand("Wing margin", outline + [outline[0]], 0.002, vein, pivot)
            strand(
                "Leading vein",
                [(0, 0, 0), (0.20, side * length * 0.5, 0.02), (0.40, side * length, 0)],
                0.0024,
                vein,
                pivot,
            )
            for t in (0.35, 0.6, 0.78):
                strand(
                    "Cross vein",
                    [
                        (0.40 * t, side * length * t, 0.02),
                        (0.40 * t + 0.1 * math.sin(math.pi * t), side * length * t, 0.02),
                    ],
                    0.0018,
                    vein,
                    pivot,
                )
            wings.append((pivot, side, hind))
    legs = []
    for side in (-1, 1):
        for i in range(3):
            x = -0.25 + i * 0.20
            pivot = bpy.data.objects.new("Leg hip", None)
            bpy.context.collection.objects.link(pivot)
            pivot.location = (x, side * 0.145, -0.10)
            sweep = -0.12 + i * 0.13
            points = [
                (0, 0, 0),
                (sweep, side * 0.115, -0.105),
                (sweep - 0.045, side * 0.19, -0.275),
                (sweep - 0.11, side * 0.21, -0.285),
            ]
            strand("Femur", points[:2], 0.014, black, pivot)
            strand("Slender tibia", points[1:3], 0.009, black, pivot)
            strand("Tarsus", points[2:], 0.0055, black, pivot)
            legs.append((pivot, side, Vector(points[-1])))
    rig = bpy.data.objects.new("Bee orientation", None)
    bpy.context.collection.objects.link(rig)
    for obj in list(bpy.context.scene.objects):
        if obj != rig and obj.parent is None:
            obj.parent = rig
    bpy.ops.object.camera_add(location=(-2.6, -7.0, 3.6))
    camera = bpy.context.object
    camera.rotation_euler = (
        (Vector((0.045, 0, 0)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    )
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 2.45
    scene = bpy.context.scene
    scene.camera = camera
    for name, location, energy, size, color in (
        ("Key", (-3.5, -4.5, 6), 520, 4, (1, 0.83, 0.74)),
        ("Fill", (3, -2, 3), 300, 3, (0.58, 0.74, 1)),
        ("Rim", (0, 2, 4.5), 360, 2.5, (1, 0.60, 0.43)),
    ):
        bpy.ops.object.light_add(type="AREA", location=location)
        obj = bpy.context.object
        obj.name = name
        obj.data.energy, obj.data.size, obj.data.color = energy, size, color
        obj.rotation_euler = (-obj.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.engine = "CYCLES"
    scene.cycles.samples = int(os.environ.get("BEE_SAMPLES", "40"))
    scene.cycles.use_denoising = True
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4
    scene.render.resolution_x = scene.render.resolution_y = int(os.environ.get("BEE_SIZE", "192"))
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.use_motion_blur = True
    scene.render.motion_blur_shutter = 0.8
    scene.view_settings.look = "AgX - Medium High Contrast"
    return scene, rig, wings, legs


def pose(scene, rig, wings, legs, index, heading):
    landing = max(0, (index - 7) / 8)
    deployed = min(1, landing / 0.35)
    tuck = max(0, (landing - 0.35) / 0.65)
    rig.rotation_euler.z = math.radians(-heading)
    for pivot, side, foot in legs:
        pivot.rotation_euler.x = side * (1 - deployed) * 0.95
        pivot.rotation_euler.y = -(1 - deployed) * 0.35
    for pivot, side, hind in wings:
        pivot.animation_data_clear()
        for time in (-0.5, 0, 0.5):
            phase = index * math.tau / 8 + time * 2.8
            pivot.rotation_euler.x = side * (
                (1 - tuck) * 0.85 * math.sin(phase + hind * 0.16) + tuck * 0.10
            )
            pivot.rotation_euler.z = -side * tuck * 1.15
            pivot.keyframe_insert(data_path="rotation_euler", frame=2 + time)
    scene.frame_set(2)
    bpy.context.view_layer.update()
    feet = []
    for pivot, _, foot in legs:
        p = world_to_camera_view(scene, scene.camera, pivot.matrix_world @ foot)
        feet.append([p.x, 1 - p.y])
    return feet


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    scene, rig, wings, legs = build()
    selection = os.environ.get("BEE_POSES")
    indices = [int(i) for i in selection.split(",")] if selection else list(range(16))
    headings = [
        int(i) for i in os.environ.get("BEE_HEADINGS", ",".join(map(str, HEADINGS))).split(",")
    ]
    records, contacts = [], {}
    for heading in headings:
        for i in indices:
            feet = pose(scene, rig, wings, legs, i, heading)
            name = f"bee_{i:02d}.png" if heading == 0 else f"bee_{i:02d}_{heading:+03d}.png"
            scene.render.filepath = str(OUTPUT / name)
            bpy.ops.render.render(write_still=True)
            records.append(name)
            if i == 15:
                contacts[str(heading)] = {
                    "feet": feet,
                    "anchor": [sum(p[axis] for p in feet) / len(feet) for axis in range(2)],
                }
    if not selection:
        pose(scene, rig, wings, legs, 15, 0)
        bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / "blender/idle_bee.blend"))
    (OUTPUT / "manifest.json").write_text(
        json.dumps(
            {
                "frames": records,
                "flight_frames": 8,
                "landing_frames": 9,
                "headings": headings,
                "contacts": contacts,
                "author": "Blender 5.2 / bake_idle_bee.py",
                "size": scene.render.resolution_x,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
