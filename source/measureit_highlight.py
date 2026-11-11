"""Non-destructive, modal viewport inspection of counted Area measurements."""

import math

import bpy
import bmesh
import blf
import gpu
from bpy.types import Operator, SpaceView3D
from gpu_extras.batch import batch_for_shader
from bpy_extras.view3d_utils import location_3d_to_region_2d

from .measureit_area_totals import collect_areas


def counted_polygons(context, with_names=False):
    """Snapshot the same visible, measured faces used by Area Totals."""
    seen = set()
    polygons = []
    for collection in (context.scene.measureit_room_collection,
                       context.scene.measureit_wall_collection):
        included, _ = collect_areas(context, collection)
        for obj, measure, _value in included:
            identity = (obj.as_pointer(), measure.as_pointer())
            if identity in seen:
                continue
            seen.add(identity)
            if obj.mode == 'EDIT':
                mesh = bmesh.from_edit_mesh(obj.data)
                mesh.verts.ensure_lookup_table()
                coords = [vert.co.copy() for vert in mesh.verts]
            else:
                coords = [vert.co.copy() for vert in obj.data.vertices]
            for face in measure.measureit_faces:
                indices = [item.glidx for item in face.measureit_index]
                if len(indices) < 3 or any(i < 0 or i >= len(coords) for i in indices):
                    continue
                polygon = [obj.matrix_world @ coords[i] for i in indices]
                polygons.append((obj.name, polygon) if with_names else polygon)
    return polygons


class MEASUREIT_OT_HighlightCountedAreas(Operator):
    bl_idname = 'measureit.highlight_counted_areas'
    bl_label = 'Highlight counted areas'
    bl_description = 'Temporarily highlight measured visible areas; Esc exits without changing the scene'
    bl_options = {'INTERNAL'}

    _handler = None
    _area = None
    _polygons = None
    _named_polygons = None
    _navigating = False

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (context.area is not None and context.area.type == 'VIEW_3D'
                and scene is not None
                and not context.window_manager.measureit_area_highlight_active
                and (scene.measureit_room_collection is not None
                     or scene.measureit_wall_collection is not None))

    def invoke(self, context, event):
        self._named_polygons = counted_polygons(context, with_names=True)
        self._polygons = [polygon for _name, polygon in self._named_polygons]
        if not self._polygons:
            self.report({'WARNING'}, 'No visible Area measurements found in the chosen collections')
            return {'CANCELLED'}
        self._area = context.area
        self._navigating = False
        self._handler = SpaceView3D.draw_handler_add(self._draw, (), 'WINDOW', 'POST_PIXEL')
        context.window_manager.measureit_area_highlight_active = True
        context.window_manager.modal_handler_add(self)
        self._area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._finish(context)
            return {'CANCELLED'}
        if (self._area is None or self._area.type != 'VIEW_3D'
                or not context.window_manager.measureit_area_highlight_active):
            self._finish(context)
            return {'CANCELLED'}
        # Allow only navigation, never clicks, selection, transforms or editing.
        # Blender still handles the zoom/pan events normally.
        if event.type == 'MIDDLEMOUSE':
            self._navigating = event.value != 'RELEASE'
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE',
                          'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE', 'MOUSESMARTZOOM'} \
                or (event.type == 'MOUSEMOVE' and self._navigating):
            self._area.tag_redraw()
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self._finish(context)

    def _finish(self, context):
        if self._handler is not None:
            SpaceView3D.draw_handler_remove(self._handler, 'WINDOW')
            self._handler = None
        if context.window_manager is not None:
            context.window_manager.measureit_area_highlight_active = False
        if self._area is not None:
            self._area.tag_redraw()
        self._polygons = None
        self._named_polygons = None

    def _draw(self):
        area = self._area
        if area is None or area.type != 'VIEW_3D':
            return
        if bpy.context.area != area:
            return
        region = bpy.context.region
        if region is None or region.type != 'WINDOW':
            return
        rv3d = bpy.context.region_data
        if rv3d is None or region.width < 2 or region.height < 2:
            return
        try:
            gpu.state.blend_set('ALPHA')
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            # Overlay is screen-only and never touches materials or visibility.
            dim = batch_for_shader(shader, 'TRIS', {'pos': [
                (0, 0), (region.width, 0), (region.width, region.height),
                (0, 0), (region.width, region.height), (0, region.height),
            ]})
            shader.bind()
            shader.uniform_float('color', (0.04, 0.04, 0.04, 0.52))
            dim.draw(shader)

            # A two-pass stroke remains legible across light and dark meshes.
            poly_shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
            poly_shader.bind()
            poly_shader.uniform_float('viewportSize', (region.width, region.height))
            for width, color in ((6.0, (0.01, 0.08, 0.02, 0.95)),
                                 (3.5, (0.15, 1.0, 0.26, 1.0))):
                segments = []
                for polygon in self._polygons or ():
                    points = [location_3d_to_region_2d(region, rv3d, p) for p in polygon]
                    if any(p is None or not math.isfinite(p.x) or not math.isfinite(p.y)
                           for p in points):
                        continue
                    for a, b in zip(points, points[1:] + points[:1]):
                        # Avoid sending huge near-plane coordinates into GPU rasterization.
                        if max(abs(a.x), abs(a.y), abs(b.x), abs(b.y)) > 100000:
                            continue
                        segments.extend([(a.x, a.y), (b.x, b.y)])
                if segments:
                    poly_shader.uniform_float('lineWidth', width)
                    poly_shader.uniform_float('color', color)
                    batch_for_shader(poly_shader, 'LINES', {'pos': segments}).draw(poly_shader)

            # Outliner object names, centered over the counted surfaces. The
            # labels belong only to this GPU overlay, never to the mesh.
            font_id = 0
            blf.size(font_id, 15)
            for name, polygon in self._named_polygons or ():
                points = [location_3d_to_region_2d(region, rv3d, p) for p in polygon]
                if any(p is None or not math.isfinite(p.x) or not math.isfinite(p.y)
                       for p in points):
                    continue
                x = sum(p.x for p in points) / len(points)
                y = sum(p.y for p in points) / len(points)
                if not (0 <= x <= region.width and 0 <= y <= region.height):
                    continue
                text_width, text_height = blf.dimensions(font_id, name)
                padding = 7
                left = x - text_width / 2 - padding
                right = x + text_width / 2 + padding
                bottom = y - text_height / 2 - padding
                top = y + text_height / 2 + padding
                backdrop = batch_for_shader(shader, 'TRIS', {'pos': [
                    (left, bottom), (right, bottom), (right, top),
                    (left, bottom), (right, top), (left, top),
                ]})
                shader.bind()
                shader.uniform_float('color', (0.02, 0.10, 0.04, 0.86))
                backdrop.draw(shader)
                blf.color(font_id, 0.88, 1.0, 0.88, 1.0)
                blf.position(font_id, x - text_width / 2, y - text_height / 2, 0)
                blf.draw(font_id, name)
        finally:
            gpu.state.blend_set('NONE')
