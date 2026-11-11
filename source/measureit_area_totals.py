"""Scene-wide area totals scoped to collections and View Layer visibility."""

import bpy
import bmesh
from mathutils import Vector
from bpy_extras import mesh_utils


def _area_value(obj, measure, scene):
    """Calculate the same triangulated world-space area as the Area overlay."""
    if obj.type != 'MESH':
        return 0.0
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        coords = [v.co.copy() for v in bm.verts]
    else:
        coords = [v.co for v in obj.data.vertices]
    total = 0.0
    for face in measure.measureit_faces:
        ids = [item.glidx for item in face.measureit_index]
        if len(ids) < 3 or any(i < 0 or i >= len(coords) for i in ids):
            continue
        if len(ids) == 3:
            triangles = [(0, 1, 2)]
        else:
            triangles = mesh_utils.ngon_tessellate(coords, ids)
        for a, b, c in triangles:
            p1, p2, p3 = (obj.matrix_world @ Vector(coords[ids[i]]) for i in (a, b, c))
            total += ((p2 - p1).cross(p3 - p1)).length * 0.5
    factor = scene.measureit_scale_factor if scene.measureit_scale else 1.0
    return total * factor * factor * scene.unit_settings.scale_length


def collect_areas(context, collection):
    """Return (included, excluded), each entry (object, measure, value/reason)."""
    included, excluded = [], []
    if collection is None:
        return included, excluded
    for obj in collection.all_objects:
        if not hasattr(obj, 'MeasureGenerator') or not obj.MeasureGenerator:
            continue
        measures = obj.MeasureGenerator[0].measureit_segments
        for measure in measures:
            if measure.gltype != 20 or measure.glfree:
                continue
            if not obj.visible_get(view_layer=context.view_layer):
                excluded.append((obj, measure, 'Object hidden in View Layer'))
            elif not measure.glview:
                excluded.append((obj, measure, 'Area measurement hidden'))
            elif not len(measure.measureit_faces):
                excluded.append((obj, measure, 'No measured faces'))
            else:
                included.append((obj, measure, _area_value(obj, measure, context.scene)))
    return included, excluded


def draw_area_totals(layout, context):
    scene = context.scene
    box = layout.box()
    header = box.row(align=True)
    header.prop(scene, 'measureit_area_totals_open', text='', icon='TRIA_DOWN' if scene.measureit_area_totals_open else 'TRIA_RIGHT', emboss=False)
    header.label(text='Area Totals', icon='DRIVER_DISTANCE')
    if not scene.measureit_area_totals_open:
        return

    fmt = '%1.' + str(scene.measureit_gl_precision) + 'f'
    totals = []
    for title, prop, detail_prop in (
        ('Room surfaces', 'measureit_room_collection', 'measureit_room_details'),
        ('Wall surfaces', 'measureit_wall_collection', 'measureit_wall_details'),
        ('Outdoor surfaces', 'measureit_outdoor_collection', 'measureit_outdoor_details'),
    ):
        box.label(text=title)
        box.prop(scene, prop, text='Collection')
        collection = getattr(scene, prop)
        if collection is None:
            box.label(text='Choose a collection', icon='INFO')
            continue
        included, excluded = collect_areas(context, collection)
        value = sum(entry[2] for entry in included)
        totals.append(value)
        row = box.row(align=True)
        row.label(text=f'{len(included)} visible Area measurements')
        row.label(text=_format_area(fmt, scene, value))
        row = box.row()
        expanded = getattr(scene, detail_prop)
        row.prop(scene, detail_prop, text=f'Surfaces included / excluded ({len(included)} / {len(excluded)})',
                 icon='TRIA_DOWN' if expanded else 'TRIA_RIGHT', emboss=False)
        if expanded:
            for obj, measure, amount in included:
                row = box.row(align=True)
                row.label(text=obj.name, icon='CHECKMARK')
                row.label(text=_format_area(fmt, scene, amount))
            for obj, measure, reason in excluded:
                row = box.row(align=True)
                row.label(text=obj.name, icon='HIDE_ON')
                row.label(text=reason)
    if totals:
        box.separator()
        row = box.row(align=True)
        row.label(text='Total visible surface')
        row.label(text=_format_area(fmt, scene, sum(totals)))
        box.separator()
        if context.window_manager.measureit_area_highlight_active:
            box.label(text='Highlight active: press Esc to exit', icon='INFO')
        else:
            box.operator('measureit.highlight_counted_areas',
                         text='Highlight counted areas', icon='HIDE_OFF')
            box.label(text='Rooms: green  |  Walls: white  |  Outdoor: blue')
            box.label(text='Esc to exit; selection and editing locked.')


def _format_area(fmt, scene, value):
    # Import lazily: geometry depends on Blender's registered MeasureIt types.
    from .measureit_geometry import format_distance
    return format_distance(fmt, scene.measureit_units, value, 2)
