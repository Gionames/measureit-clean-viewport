# SPDX-FileCopyrightText: 2016-2022 Blender Foundation
#
# SPDX-License-Identifier: GPL-2.0-or-later

# ----------------------------------------------------------
# File: measureit_main.py
# Main panel for different Measureit general actions
# Author: Antonio Vazquez (antonioya)
#
# ----------------------------------------------------------
# noinspection PyUnresolvedReferences
import bpy
import bmesh
from functools import wraps
from math import degrees
from bmesh import from_edit_mesh
# noinspection PyUnresolvedReferences
from bpy.types import PropertyGroup, Panel, Object, Operator, SpaceView3D
from bpy.props import IntProperty, CollectionProperty, FloatVectorProperty, BoolProperty, StringProperty, \
                      FloatProperty, EnumProperty
from bpy.app.handlers import persistent
# noinspection PyUnresolvedReferences
from .measureit_geometry import *
from .measureit_render import *
from .measureit_area_totals import draw_area_totals


def _eligible_edit_geometry(obj, operator_id):
    """Skip edit meshes without the selection requested by a measure tool."""
    if obj.type != 'MESH' or obj.mode != 'EDIT':
        return False
    bm = bmesh.from_edit_mesh(obj.data)
    if operator_id == 'measureit.addarea':
        return any(face.select for face in bm.faces)
    if operator_id == 'measureit.addsegment':
        return sum(1 for vert in bm.verts if vert.select) >= 2
    if operator_id in {'measureit.addangle', 'measureit.addarc'}:
        return len(bm.select_history) == 3
    if operator_id == 'measureit.addlabel':
        return sum(1 for vert in bm.verts if vert.select) == 1
    if operator_id in {'measureit.addsegmentorto', 'measureit.addarcchain'}:
        return any(vert.select for vert in bm.verts) or any(edge.select for edge in bm.edges)
    return False


def multi_object_edit(execute_one):
    """Apply a legacy single-object measurement operator to every edited mesh.

    The active object is restored even if an individual operator fails. This
    preserves the familiar single-object behavior and avoids mixed-object
    vertex indices in stored measurements.
    """
    @wraps(execute_one)
    def run(self, context):
        if context.mode != 'EDIT_MESH':
            return execute_one(self, context)
        edited = tuple(obj for obj in context.objects_in_mode if obj.type == 'MESH')
        if len(edited) <= 1:
            return execute_one(self, context)
        operator_id = self.bl_idname.lower().replace('_ot_', '.')
        eligible = [obj for obj in edited if _eligible_edit_geometry(obj, operator_id)]
        if not eligible:
            self.report({'WARNING'}, 'No suitable selected geometry in the edited objects')
            return {'CANCELLED'}
        original_active = context.view_layer.objects.active
        changed_objects = 0
        new_measurements = 0
        try:
            for obj in eligible:
                before = (len(obj.MeasureGenerator[0].measureit_segments)
                          if obj.MeasureGenerator else 0)
                with context.temp_override(object=obj, active_object=obj):
                    result = execute_one(self, bpy.context)
                after = (len(obj.MeasureGenerator[0].measureit_segments)
                         if obj.MeasureGenerator else 0)
                if 'FINISHED' in result and after > before:
                    changed_objects += 1
                    new_measurements += after - before
        finally:
            if original_active is not None:
                context.view_layer.objects.active = original_active
        if context.area is not None:
            context.area.tag_redraw()
        if new_measurements:
            self.report({'INFO'}, f'Added {new_measurements} measurements on '
                        f'{changed_objects} of {len(edited)} edited objects')
            return {'FINISHED'}
        self.report({'INFO'}, 'No new measurements (selection may already be measured)')
        return {'CANCELLED'}
    return run


# ------------------------------------------------------
# Handler to detect new Blend load
#
# ------------------------------------------------------
# noinspection PyUnusedLocal
@persistent
def load_handler(dummy):
    MEASUREIT_OT_RunHintDisplay.handle_remove(None, bpy.context)


# ------------------------------------------------------
# Handler to detect save Blend
# Clear not used measured
#
# ------------------------------------------------------
# noinspection PyUnusedLocal
@persistent
def save_handler(dummy):
    # noinspection PyBroadException
    try:
        print("MeasureIt: Cleaning data")
        objlist = bpy.context.scene.objects
        for myobj in objlist:
            if hasattr(myobj, "MeasureGenerator") and len(myobj.MeasureGenerator) > 0:
                mp = myobj.MeasureGenerator[0]
                x = 0
                for ms in mp.measureit_segments:
                    ms.name = "segment_" + str(x)
                    x += 1
                    if ms.glfree is True:
                        idx = mp.measureit_segments.find(ms.name)
                        if idx > -1:
                            print("MeasureIt: Removed segment not used")
                            mp.measureit_segments.remove(idx)

                # reset size
                mp.measureit_num = len(mp.measureit_segments)
    except:
        pass


bpy.app.handlers.load_post.append(load_handler)
bpy.app.handlers.save_pre.append(save_handler)


# ------------------------------------------------------------------
# Define property group class for measureit faces index
# ------------------------------------------------------------------
class MeasureitIndex(PropertyGroup):
    glidx: IntProperty(name="index", description="vertex index")


# Register
bpy.utils.register_class(MeasureitIndex)


# ------------------------------------------------------------------
# Define property group class for measureit faces
# ------------------------------------------------------------------
class MeasureitFaces(PropertyGroup):
    glface: IntProperty(name="glface", description="Face number")
    # Array of index
    measureit_index: CollectionProperty(type=MeasureitIndex)


# Register
bpy.utils.register_class(MeasureitFaces)


def update_arc_chain_selection(self, context):
    """Reflect the linear-length mode and connector exclusions in Edit Mode."""
    if not self.chain_select_edges or not self.chain_selection_saved:
        return
    obj = getattr(self, 'id_data', None)
    if obj is None or obj.type != 'MESH' or obj.mode != 'EDIT':
        return
    if self.gltype != 21:
        return
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    indices = [item.glidx for item in self.chain_indices]
    if len(indices) < 2 or any(i < 0 or i >= len(bm.verts) for i in indices):
        return
    points = [Vector(get_point(bm.verts[i].co, obj)) for i in indices]
    left, right = chain_included_bounds(points, self.chain_auto_trim,
                                        self.chain_mode, self.chain_trim_start,
                                        self.chain_trim_end)
    count = len(indices) - 1
    selection_mode = context.tool_settings.mesh_select_mode
    if selection_mode[2]:
        return
    # Rebuild the temporary highlight from scratch after every property edit.
    # The old selection has already been saved by the toggle callback.
    for e in bm.edges:
        e.select_set(False)
    for v in bm.verts:
        v.select_set(False)
    # Select precisely the edges used by every displayed measurement.
    for j, (a, b) in enumerate(zip(indices, indices[1:])):
        edge = next((e for e in bm.verts[a].link_edges
                     if bm.verts[b] in e.verts), None)
        if edge is not None:
            if left <= j < count - right:
                if selection_mode[0] and not selection_mode[1]:
                    bm.verts[a].select_set(True)
                    bm.verts[b].select_set(True)
                else:
                    edge.select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)
    if context.area is not None:
        context.area.tag_redraw()


def toggle_arc_chain_selection(self, context):
    """Temporary selection overlay; OFF restores the original edge/vertex set."""
    obj = getattr(self, 'id_data', None)
    if obj is None or obj.type != 'MESH' or obj.mode != 'EDIT':
        return
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.edges.index_update()
    if context.tool_settings.mesh_select_mode[2]:
        return
    if self.chain_select_edges:
        self.chain_saved_edges.clear()
        self.chain_saved_vertices.clear()
        for e in bm.edges:
            if e.select:
                self.chain_saved_edges.add().glidx = e.index
        for v in bm.verts:
            if v.select:
                self.chain_saved_vertices.add().glidx = v.index
        self.chain_selection_saved = True
        update_arc_chain_selection(self, context)
    elif self.chain_selection_saved:
        for e in bm.edges:
            e.select_set(False)
        for v in bm.verts:
            v.select_set(False)
        for v in self.chain_saved_vertices:
            if 0 <= v.glidx < len(bm.verts):
                bm.verts[v.glidx].select_set(True)
        for e in self.chain_saved_edges:
            if 0 <= e.glidx < len(bm.edges):
                bm.edges[e.glidx].select_set(True)
        self.chain_saved_edges.clear()
        self.chain_saved_vertices.clear()
        self.chain_selection_saved = False
        bmesh.update_edit_mesh(obj.data)
        if context.area is not None:
            context.area.tag_redraw()


# ------------------------------------------------------------------
# Define property group class for measureit data
# ------------------------------------------------------------------
class MeasureitProperties(PropertyGroup):
    gltype: IntProperty(name="gltype",
                       description="Measure type (1-Segment, 2-Label, etc..)", default=1)
    glpointa: IntProperty(name="glpointa",
                         description="Hidden property for opengl")
    glpointb: IntProperty(name="glpointb",
                         description="Hidden property for opengl")
    glpointc: IntProperty(name="glpointc",
                         description="Hidden property for opengl")
    glcolor: FloatVectorProperty(name="glcolor",
                                  description="Color for the measure",
                                  default=(1.0, 1.0, 1.0, 1.0),
                                  min=0.1,
                                  max=1,
                                  subtype='COLOR',
                                  size=4)
    glview: BoolProperty(name="glview",
                          description="Measure visible/hide",
                          default=True)
    glspace: FloatProperty(name='glspace', min=-100, max=100, default=0.05,
                            precision=3,
                            description='Distance to display measure')
    glwidth: IntProperty(name='glwidth', min=1, max=10, default=1,
                          description='line width')
    glfree: BoolProperty(name="glfree",
                          description="This measure is free and can be deleted",
                          default=False)
    gltxt: StringProperty(name="gltxt", maxlen=256,
                           description="Short description (use | for line break)")
    gladvance: BoolProperty(name="gladvance",
                             description="Advanced options as line width or position",
                             default=False)
    gldefault: BoolProperty(name="gldefault",
                             description="Display measure in position calculated by default",
                             default=True)
    glnormalx: FloatProperty(name="glnormalx",
                              description="Change orientation in X axis",
                              default=1, min=-1, max=1, precision=2)
    glnormaly: FloatProperty(name="glnormaly",
                              description="Change orientation in Y axis",
                              default=0, min=-1, max=1, precision=2)
    glnormalz: FloatProperty(name="glnormalz",
                              description="Change orientation in Z axis",
                              default=0, min=-1, max=1, precision=2)
    glfont_size: IntProperty(name="Text Size",
                              description="Text size",
                              default=14, min=6, max=150)
    glfont_align: EnumProperty(items=(('L', "Left Align", ""),
                                       ('C', "Center Align", ""),
                                       ('R', "Right Align", "")),
                                name="Align Font", default='C',
                                description="Set Font Alignment")
    glfont_rotat: IntProperty(name='Rotate', min=0, max=360, default=0,
                                description="Text rotation in degrees")
    gllink: StringProperty(name="gllink",
                            description="linked object for linked measures")
    glocwarning: BoolProperty(name="glocwarning",
                               description="Display a warning if some axis is not used in distance",
                               default=True)
    glocx: BoolProperty(name="glocx",
                         description="Include changes in X axis for calculating the distance",
                         default=True)
    glocy: BoolProperty(name="glocy",
                         description="Include changes in Y axis for calculating the distance",
                         default=True)
    glocz: BoolProperty(name="glocz",
                         description="Include changes in Z axis for calculating the distance",
                         default=True)
    glfontx: IntProperty(name="glfontx",
                          description="Move the text along the dimension line (pixels at the reference zoom)",
                          default=0, min=-3000, max=3000)
    glfonty: IntProperty(name="glfonty",
                          description="Move the text perpendicular to the dimension line (pixels at the reference zoom)",
                          default=0, min=-3000, max=3000)
    gldist: BoolProperty(name="gldist",
                          description="Display distance for this measure",
                          default=True)
    glnames: BoolProperty(name="glnames",
                           description="Display text for this measure",
                           default=True)
    gltot: EnumProperty(items=(('99', "-", "Select a group for sum"),
                                ('0', "A", ""),
                                ('1', "B", ""),
                                ('2', "C", ""),
                                ('3', "D", ""),
                                ('4', "E", ""),
                                ('5', "F", ""),
                                ('6', "G", ""),
                                ('7', "H", ""),
                                ('8', "I", ""),
                                ('9', "J", ""),
                                ('10', "K", ""),
                                ('11', "L", ""),
                                ('12', "M", ""),
                                ('13', "N", ""),
                                ('14', "O", ""),
                                ('15', "P", ""),
                                ('16', "Q", ""),
                                ('17', "R", ""),
                                ('18', "S", ""),
                                ('19', "T", ""),
                                ('20', "U", ""),
                                ('21', "V", ""),
                                ('22', "W", ""),
                                ('23', "X", ""),
                                ('24', "Y", ""),
                                ('25', "Z", "")),
                         name="Sum in Group",
                         description="Add segment length in selected group")
    glorto: EnumProperty(items=(('99', "None", ""),
                                 ('0', "A", "Point A must use selected point B location"),
                                 ('1', "B", "Point B must use selected point A location")),
                          name="Orthogonal",
                          description="Display point selected as orthogonal (select axis to copy)")
    glorto_x: BoolProperty(name="ox",
                            description="Copy X location",
                            default=False)
    glorto_y: BoolProperty(name="oy",
                            description="Copy Y location",
                            default=False)
    glorto_z: BoolProperty(name="oz",
                            description="Copy Z location",
                            default=False)
    glarrow_a: EnumProperty(items=(('99', "--", "No arrow"),
                                    ('1', "Line", "The point of the arrow are lines"),
                                    ('2', "Triangle", "The point of the arrow is triangle"),
                                    ('3', "TShape", "The point of the arrow is a T")),
                             name="A end",
                             description="Add arrows to point A")
    glarrow_b: EnumProperty(items=(('99', "--", "No arrow"),
                                    ('1', "Line", "The point of the arrow are lines"),
                                    ('2', "Triangle", "The point of the arrow is triangle"),
                                    ('3', "TShape", "The point of the arrow is a T")),
                             name="B end",
                             description="Add arrows to point B")
    glarrow_s: IntProperty(name="Size",
                            description="Arrow size",
                            default=15, min=6, max=500)

    glarc_full: BoolProperty(name="arcfull",
                              description="Create full circumference",
                              default=False)
    glarc_extrad: BoolProperty(name="arcextrad",
                                description="Adapt radio length to arc line",
                                default=True)
    glarc_rad: BoolProperty(name="arc rad",
                             description="Show arc radius",
                             default=True)
    glarc_len: BoolProperty(name="arc len",
                             description="Show arc length",
                             default=True)
    glarc_ang: BoolProperty(name="arc ang",
                             description="Show arc angle",
                             default=True)
    chain_indices: CollectionProperty(type=MeasureitIndex)
    chain_saved_edges: CollectionProperty(type=MeasureitIndex)
    chain_saved_vertices: CollectionProperty(type=MeasureitIndex)
    chain_selection_saved: BoolProperty(default=False)
    chain_select_edges: BoolProperty(name="Select all edges included",
                                     description="Temporarily select measured edges; turn off to restore the previous selection",
                                     default=False, update=toggle_arc_chain_selection)
    chain_mode: EnumProperty(name="Linear segment length", items=(
        ('ALL', "With connectors", "Sum every edge, including connectors"),
        ('ARC', "Without connectors", "Sum only the circular portion")),
        default='ALL', update=update_arc_chain_selection)
    chain_radius: BoolProperty(name="Show estimated radius", default=True)
    chain_angle: BoolProperty(name="Show estimated angle", default=True)
    chain_auto_trim: BoolProperty(name="Detect connectors", default=True,
                                  update=update_arc_chain_selection)
    chain_trim_start: IntProperty(name="Additional start edges to exclude", min=0, max=100, default=0,
                                  update=update_arc_chain_selection)
    chain_trim_end: IntProperty(name="Additional end edges to exclude", min=0, max=100, default=0,
                                update=update_arc_chain_selection)

    glarc_a: EnumProperty(items=(('99', "--", "No arrow"),
                                  ('1', "Line", "The point of the arrow are lines"),
                                  ('2', "Triangle", "The point of the arrow is triangle"),
                                  ('3', "TShape", "The point of the arrow is a T")),
                           name="Ar end",
                           description="Add arrows to point A")
    glarc_b: EnumProperty(items=(('99', "--", "No arrow"),
                                  ('1', "Line", "The point of the arrow are lines"),
                                  ('2', "Triangle", "The point of the arrow is triangle"),
                                  ('3', "TShape", "The point of the arrow is a T")),
                           name="Br end",
                           description="Add arrows to point B")
    glarc_s: IntProperty(name="Size",
                          description="Arrow size",
                          default=15, min=6, max=500)
    glarc_txradio: StringProperty(name="txradio",
                                   description="Text for radius", default="r=")
    glarc_txlen: StringProperty(name="txlen",
                                 description="Text for length", default="L=")
    glarc_txang: StringProperty(name="txang",
                                 description="Text for angle", default="A=")
    glcolorarea: FloatVectorProperty(name="glcolorarea",
                                      description="Color for the measure of area",
                                      default=(1.0, 1.0, 1.0, 1.0),
                                      min=0.1,
                                      max=1,
                                      subtype='COLOR',
                                      size=4)

    # Array of faces
    measureit_faces: CollectionProperty(type=MeasureitFaces)


# Register
bpy.utils.register_class(MeasureitProperties)


# ------------------------------------------------------------------
# Define object class (container of segments)
# Measureit
# ------------------------------------------------------------------
class MeasureContainer(PropertyGroup):
    measureit_num: IntProperty(name='Number of measures', min=0, max=1000, default=0,
                                description='Number total of measureit elements')
    # Array of segments
    measureit_segments: CollectionProperty(type=MeasureitProperties)


bpy.utils.register_class(MeasureContainer)
Object.MeasureGenerator = CollectionProperty(type=MeasureContainer)


# ------------------------------------------------------------------
# Define UI class
# Measureit
# ------------------------------------------------------------------
class MEASUREIT_PT_Edit(Panel):
    bl_idname = "MEASUREIT_PT_Edit"
    bl_label = "Items"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category= 'View'
    bl_parent_id = 'MEASUREIT_PT_Main'

    # -----------------------------------------------------
    # Verify if visible
    # -----------------------------------------------------
    @classmethod
    def poll(cls, context):
        # Area Totals are scene-wide, even when the selected object has no measures.
        return context.scene is not None

    # -----------------------------------------------------
    # Draw (create UI interface)
    # -----------------------------------------------------
    # noinspection PyUnusedLocal
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        if context.object is not None:
            if hasattr(context.object, "MeasureGenerator") and len(context.object.MeasureGenerator) > 0:
                box = layout.box()
                row = box.row()
                row.label(text=context.object.name)
                row = box.row()
                row.prop(scene, 'measureit_gl_precision', text="Precision")
                row.prop(scene, 'measureit_units')
                row = box.row()
                row.prop(scene, 'measureit_gl_show_d', text="Distances", toggle=True, icon="ALIGN_CENTER")
                row.prop(scene, 'measureit_gl_show_n', text="Texts", toggle=True, icon="FONT_DATA")
                row = box.row()
                row.prop(scene, 'measureit_hide_units', text="Hide measurement unit")
                row.prop(scene, 'measureit_text_only', text="Text Only", toggle=True, icon="FONT_DATA")
                row = box.row(align=True)
                row.label(text="Font scale: %+d (x%.2f)" % (scene.measureit_font_offset, 1.1 ** scene.measureit_font_offset))
                row.operator('measureit.fontzoom', text="-").action = 'MINUS'
                row.operator('measureit.fontzoom', text="+").action = 'PLUS'
                row = box.row()
                row.operator('measureit.fontzoom', text="Reset zoom reference",
                             icon="ZOOM_ALL").action = 'RESET'
                # Scale factor
                row = box.row()
                row.prop(scene, 'measureit_scale', text="Scale")
                if scene.measureit_scale is True:
                    split = row.split(factor=0.25, align=False)
                    split.prop(scene, 'measureit_scale_color', text="")
                    split.prop(scene, 'measureit_scale_factor', text="1")
                    row = box.row()
                    row.separator()
                    row.prop(scene, 'measureit_gl_scaletxt', text="")
                    row.prop(scene, 'measureit_scale_font')
                    row.prop(scene, 'measureit_scale_precision', text="")
                    row = box.row()
                    row.separator()
                    row.prop(scene, 'measureit_scale_pos_x')
                    row.prop(scene, 'measureit_scale_pos_y')

                # Override
                row = box.row()
                row.prop(scene, 'measureit_ovr', text="Override")
                if scene.measureit_ovr is True:
                    split = row.split(factor=0.25, align=False)
                    split.prop(scene, 'measureit_ovr_color', text="")
                    split.prop(scene, 'measureit_ovr_width', text="Width")
                    row = box.row()
                    row.separator()
                    row.prop(scene, 'measureit_ovr_font', text="Font")
                    row.prop(scene, 'measureit_ovr_font_align', text="")
                    if scene.measureit_ovr_font_align == 'L':
                        row.prop(scene, 'measureit_ovr_font_rotation', text="Rotate")

                mp = context.object.MeasureGenerator[0]
                # -----------------
                # loop
                # -----------------
                if mp.measureit_num > 0:
                    box = layout.box()
                    row = box.row(align=True)
                    row.operator("measureit.expandallsegment", text="Expand all", icon="ZOOM_IN")
                    row.operator("measureit.collapseallsegment", text="Collapse all", icon="ZOOM_OUT")
                    for idx in range(mp.measureit_num):
                        if mp.measureit_segments[idx].glfree is False:
                            add_item(box, idx, mp.measureit_segments[idx])

                row = box.row()
                row.operator("measureit.deleteallsegment", text="Delete all", icon="X")
                # -----------------
                # Sum loop segments
                # -----------------
                if mp.measureit_num > 0:
                    scale = bpy.context.scene.unit_settings.scale_length
                    tx = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S",
                          "T", "U", "V", "W", "X", "Y", "Z"]
                    tot = [0.0] * len(tx)
                    ac = [False] * len(tx)
                    myobj = context.object
                    obverts = get_mesh_vertices(myobj)
                    viewtot = False
                    for idx in range(mp.measureit_num):
                        ms = mp.measureit_segments[idx]
                        if (ms.gltype == 1 or ms.gltype == 12
                            or ms.gltype == 13 or ms.gltype == 14) and ms.gltot != '99' \
                                and ms.glfree is False:  # only segments
                            if bpy.context.mode == "EDIT_MESH":
                                bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
                                if hasattr(bm.verts, "ensure_lookup_table"):
                                    bm.verts.ensure_lookup_table()
                            if ms.glpointa <= len(obverts) and ms.glpointb <= len(obverts):
                                p1 = get_point(obverts[ms.glpointa].co, myobj)
                                if ms.gltype == 1:
                                    p2 = get_point(obverts[ms.glpointb].co, myobj)
                                elif ms.gltype == 12:
                                    p2 = get_point((0.0,
                                                    obverts[ms.glpointa].co[1],
                                                    obverts[ms.glpointa].co[2]), myobj)
                                elif ms.gltype == 13:
                                    p2 = get_point((obverts[ms.glpointa].co[0],
                                                    0.0,
                                                    obverts[ms.glpointa].co[2]), myobj)
                                else:
                                    p2 = get_point((obverts[ms.glpointa].co[0],
                                                    obverts[ms.glpointa].co[1],
                                                    0.0), myobj)

                                dist, distloc = distance(p1, p2, ms.glocx, ms.glocy, ms.glocz)
                                if dist == distloc:
                                    usedist = dist
                                else:
                                    usedist = distloc
                                usedist *= scale
                                tot[int(ms.gltot)] += usedist
                                ac[int(ms.gltot)] = True
                                viewtot = True
                    # -----------------
                    # Print values
                    # -----------------
                    if viewtot is True:
                        pr = scene.measureit_gl_precision
                        fmt = "%1." + str(pr) + "f"
                        units = scene.measureit_units

                        box = layout.box()
                        box.label(text="Totals", icon='SOLO_ON')
                        final = 0
                        for idx in range(len(tot)):
                            if ac[idx] is True:
                                final += tot[idx]
                                tx_dist = format_distance(fmt, units, tot[idx])
                                row = box.row(align=True)
                                row.label(text="Group " + tx[idx] + ":")
                                row.label(text=" ")
                                row.label(text=tx_dist)

                        # Grand total
                        row = box.row(align=True)
                        row.label(text="")
                        row.label(text=" ")
                        row.label(text="-" * 20)
                        tx_dist = format_distance(fmt, units, final)

                        row = box.row(align=True)
                        row.label(text="")
                        row.label(text=" ")
                        row.label(text=tx_dist)
                        # delete all
                        row = box.row()
                        row.operator("measureit.deleteallsum", text="Delete all", icon="X")

        # At the bottom of Items, regardless of the active object.
        draw_area_totals(layout, context)


# -----------------------------------------------------
# Add segment options to the panel.
# -----------------------------------------------------
def add_item(box, idx, segment):
    scene = bpy.context.scene
    row = box.row(align=True)
    if segment.glview is True:
        icon = "HIDE_OFF"
    else:
        icon = "HIDE_ON"

    row.prop(segment, 'glview', text="", toggle=True, icon=icon)
    row.prop(segment, 'gladvance', text="", toggle=True, icon="PREFERENCES")
    if segment.gltype == 20:  # Area special
        split = row.split(factor=0.15, align=True)
        split.prop(segment, 'glcolorarea', text="")
        split = split.split(factor=0.20, align=True)
        split.prop(segment, 'glcolor', text="")
    else:
        split = row.split(factor=0.25, align=True)
        split.prop(segment, 'glcolor', text="")
    split.prop(segment, 'gltxt', text="")
    op = row.operator("measureit.deletesegment", text="", icon="X")
    op.tag = idx  # saves internal data
    if segment.gladvance is True:
        row = box.row(align=True)
        row.prop(segment, 'glfont_size', text="Font")
        row.prop(segment, 'glfont_align', text="")
        if segment.glfont_align == 'L':
            row.prop(segment, 'glfont_rotat', text="Rotate")
        row = box.row(align=True)
        if segment.gltype != 9 and segment.gltype != 10 and segment.gltype != 20:
            row.prop(segment, 'glspace', text="Distance")
        row.prop(segment, 'glfontx', text="X")
        row.prop(segment, 'glfonty', text="Y")

        if segment.gltype == 21:
            is_editing = bpy.context.mode == 'EDIT_MESH'
            row = box.row(align=True)
            row.enabled = is_editing and not bpy.context.tool_settings.mesh_select_mode[2]
            row.prop(segment, 'chain_select_edges', text="Select all edges included", toggle=True)
            row = box.row(align=True)
            row.enabled = is_editing
            row.prop(segment, 'chain_auto_trim')
            row = box.row(align=True)
            row.enabled = is_editing
            row.label(text="Linear segment length")
            if segment.chain_auto_trim:
                row.prop(segment, 'chain_mode', text="")
            else:
                sub = row.row(align=True)
                sub.enabled = False
                sub.label(text="All segments")
            row = box.row(align=True)
            row.prop(segment, 'chain_radius')
            row.prop(segment, 'chain_angle')
            row = box.row(align=True)
            row.enabled = is_editing and segment.chain_auto_trim
            row.prop(segment, 'chain_trim_start')
            row.prop(segment, 'chain_trim_end')

        # Arrows
        if segment.gltype != 9 and segment.gltype != 10 and segment.gltype != 20:
            row = box.row(align=True)
            row.prop(segment, 'glarrow_a', text="")
            row.prop(segment, 'glarrow_b', text="")
            if segment.glarrow_a != '99' or segment.glarrow_b != '99':
                row.prop(segment, 'glarrow_s', text="Size")

        if segment.gltype != 2 and segment.gltype != 10:
            row = box.row(align=True)
            if scene.measureit_gl_show_d is True and segment.gltype != 9:
                row.prop(segment, 'gldist', text="Distance", toggle=True, icon="ALIGN_CENTER")
            if scene.measureit_gl_show_n is True:
                row.prop(segment, 'glnames', text="Text", toggle=True, icon="FONT_DATA")
            # sum distances
            if segment.gltype == 1 or segment.gltype == 12 or segment.gltype == 13 or segment.gltype == 14:
                row.prop(segment, 'gltot', text="Sum")

        if segment.gltype != 9 and segment.gltype != 10 and segment.gltype != 20:
            row = box.row(align=True)
            row.prop(segment, 'glwidth', text="Line")
            row.prop(segment, 'gldefault', text="Automatic position")
            if segment.gldefault is False:
                row = box.row(align=True)
                row.prop(segment, 'glnormalx', text="X")
                row.prop(segment, 'glnormaly', text="Y")
                row.prop(segment, 'glnormalz', text="Z")

        # Loc axis
        if segment.gltype != 2 and segment.gltype != 9 and segment.gltype != 10 \
                and segment.gltype != 11 and segment.gltype != 12 and segment.gltype != 13 \
                and segment.gltype != 14 and segment.gltype != 20:
            row = box.row(align=True)
            row.prop(segment, 'glocx', text="X", toggle=True)
            row.prop(segment, 'glocy', text="Y", toggle=True)
            row.prop(segment, 'glocz', text="Z", toggle=True)
            if segment.glocx is False or segment.glocy is False or segment.glocz is False:
                row = box.row()
                if segment.gltype == 1:
                    row.prop(segment, 'glorto', text="Orthogonal")
                row.prop(segment, 'glocwarning', text="Warning")
                # orthogonal (only segments)
                if segment.gltype == 1:
                    if segment.glorto != "99":
                        row = box.row(align=True)
                        row.prop(segment, 'glorto_x', text="X", toggle=True)
                        row.prop(segment, 'glorto_y', text="Y", toggle=True)
                        row.prop(segment, 'glorto_z', text="Z", toggle=True)

        # Arc special
        if segment.gltype == 11:
            row = box.row(align=True)
            row.prop(segment, 'glarc_rad', text="Radius")
            row.prop(segment, 'glarc_len', text="Length")
            row.prop(segment, 'glarc_ang', text="Angle")

            row = box.row(align=True)
            row.prop(segment, 'glarc_txradio', text="")
            row.prop(segment, 'glarc_txlen', text="")
            row.prop(segment, 'glarc_txang', text="")
            row = box.row(align=True)
            row.prop(segment, 'glarc_full', text="Full Circle")
            if segment.glarc_rad is True:
                row.prop(segment, 'glarc_extrad', text="Adapt radio")

            row = box.row(align=True)
            row.prop(segment, 'glarc_a', text="")
            row.prop(segment, 'glarc_b', text="")
            if segment.glarc_a != '99' or segment.glarc_b != '99':
                row.prop(segment, 'glarc_s', text="Size")


# ------------------------------------------------------------------
# Define panel class for main functions.
# ------------------------------------------------------------------
class MEASUREIT_PT_Main(Panel):
    bl_idname = "MEASUREIT_PT_Main"
    bl_label = "MeasureIt Tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category= 'View'
    bl_options = {'DEFAULT_CLOSED'}

    # ------------------------------
    # Draw UI
    # ------------------------------
    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # ------------------------------
        # Tool Buttons
        # ------------------------------
        box = layout.box()
        # ------------------------------
        # Display Buttons
        # ------------------------------
        row = box.row()
        if context.window_manager.measureit_run_opengl is False:
            icon = 'PLAY'
            txt = 'Show'
        else:
            icon = "PAUSE"
            txt = 'Hide'

        row.operator("measureit.runopengl", text=txt, icon=icon)
        row.prop(scene, "measureit_gl_ghost", text="", icon='GHOST_ENABLED')
        if context.window_manager.measureit_run_opengl is False:
            box.label(text="Viewport measures are off: press Show", icon='INFO')

        # Tools
        box = layout.box()
        box.label(text="Add Measures")
        row = box.row()
        row.operator("measureit.addsegment", text="Segment")
        row.prop(scene, "measureit_sum", text="Sum")

        # To origin
        row = box.row()
        op = row.operator("measureit.addsegmentorto", text="X")
        op.tag = 0  # saves internal data
        op = row.operator("measureit.addsegmentorto", text="Y")
        op.tag = 1  # saves internal data
        op = row.operator("measureit.addsegmentorto", text="Z")
        op.tag = 2  # saves internal data

        row = box.row()
        row.operator("measureit.addangle", text="Angle", icon="LINCURVE")
        row.operator("measureit.addarc", text="Arc")
        row.operator("measureit.addarcchain", text="Arc Chain")

        row = box.row()
        row.operator("measureit.addlabel", text="Label", icon="FONT_DATA")
        row.operator("measureit.addnote", text="Annotation")

        row = box.row()
        row.operator("measureit.addlink", text="Link")
        row.operator("measureit.addorigin", text="Origin")

        row = box.row()
        row.operator("measureit.addarea", text="Area", icon="MESH_GRID")

        # ------------------------------
        # Debug data
        # ------------------------------
        box = layout.box()
        row = box.row(align=False)
        if scene.measureit_debug is False:
            row.prop(scene, "measureit_debug", icon="TRIA_RIGHT",
                     text="Mesh Debug", emboss=False)
        else:
            row.prop(scene, "measureit_debug", icon="TRIA_DOWN",
                     text="Mesh Debug", emboss=False)

            row = box.row()
            split = row.split(factor=0.10, align=True)
            split.prop(scene, 'measureit_debug_obj_color', text="")
            split.prop(scene, "measureit_debug_objects", icon="OBJECT_DATA")
            split.prop(scene, "measureit_debug_object_loc", icon="EMPTY_DATA")

            row = box.row()
            split = row.split(factor=0.10, align=True)
            split.prop(scene, 'measureit_debug_vert_color', text="")
            split.prop(scene, "measureit_debug_vertices", icon="VERTEXSEL")
            split.prop(scene, "measureit_debug_vert_loc", icon="EMPTY_DATA")
            if scene.measureit_debug_vert_loc is True:
                split.prop(scene, 'measureit_debug_vert_loc_toggle', text="")

            row = box.row()
            split = row.split(factor=0.10, align=True)
            split.prop(scene, 'measureit_debug_edge_color', text="")
            split = split.split(factor=0.5, align=True)
            split.prop(scene, "measureit_debug_edges", icon="EDGESEL")

            row = box.row()
            split = row.split(factor=0.10, align=True)
            split.prop(scene, 'measureit_debug_face_color', text="")
            split = split.split(factor=0.5, align=True)
            split.prop(scene, "measureit_debug_faces", icon="FACESEL")

            row = box.row()
            split = row.split(factor=0.10, align=True)
            split.prop(scene, 'measureit_debug_norm_color', text="")
            if scene.measureit_debug_normals is False:
                split = split.split(factor=0.50, align=True)
                split.prop(scene, "measureit_debug_normals", icon="OBJECT_ORIGIN")
            else:
                split = split.split(factor=0.5, align=True)
                split.prop(scene, "measureit_debug_normals", icon="OBJECT_ORIGIN")
                split.prop(scene, "measureit_debug_normal_size")
                row = box.row()
                split = row.split(factor=0.10, align=True)
                split.separator()
                split.prop(scene, "measureit_debug_normal_details")
                split.prop(scene, 'measureit_debug_width', text="Thickness")

            row = box.row(align=True)
            row.prop(scene, "measureit_debug_select", icon="GHOST_ENABLED")
            row.prop(scene, 'measureit_debug_font', text="Font")
            row.prop(scene, 'measureit_debug_precision', text="Precision")


# ------------------------------------------------------------------
# Define panel class for conf functions.
# ------------------------------------------------------------------
class MEASUREIT_PT_Conf(Panel):
    bl_idname = "MEASUREIT_PT_Conf"
    bl_label = "Configuration"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category= 'View'
    bl_parent_id = 'MEASUREIT_PT_Main'
    bl_options = {'DEFAULT_CLOSED'}

    # ------------------------------
    # Draw UI
    # ------------------------------
    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Configuration data
        box = layout.box()
        row = box.row()
        split = row.split(factor=0.2, align=True)
        split.label(text="Text")
        split = split.split(factor=0.2, align=True)
        split.prop(scene, "measureit_default_color", text="")
        split.prop(scene, "measureit_gl_txt", text="")
        row = box.row(align=True)
        row.prop(scene, "measureit_hint_space")
        row.prop(scene, "measureit_font_align", text="")
        # Arrow
        row = box.row(align=True)
        row.prop(scene, "measureit_glarrow_a", text="")
        row.prop(scene, "measureit_glarrow_b", text="")
        if scene.measureit_glarrow_a != '99' or scene.measureit_glarrow_b != '99':
            row.prop(scene, "measureit_glarrow_s", text="Size")
        row = box.row(align=True)
        row.prop(scene, "measureit_font_size")
        if scene.measureit_font_align == 'L':
            row.prop(scene, "measureit_font_rotation", text="Rotate")


# ------------------------------------------------------------------
# Define panel class for render functions.
# ------------------------------------------------------------------
class MEASUREIT_PT_Render(Panel):
    bl_idname = "MEASUREIT_PT_Render"
    bl_label = "Render"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category= 'Display'
    bl_parent_id = 'MEASUREIT_PT_Main'
    bl_options = {'DEFAULT_CLOSED'}

    # ------------------------------
    # Draw UI
    # ------------------------------
    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Render settings
        box = layout.box()
        row = box.row()
        row.prop(scene, "measureit_render_type")
        row = box.row()
        row.operator("measureit.rendersegment", icon='SCRIPT')
        row = box.row()
        row.prop(scene, "measureit_render", text="Save render image")
        row = box.row()
        row.prop(scene, "measureit_rf", text="Frame")
        if scene.measureit_rf is True:
            row.prop(scene, "measureit_rf_color", text="Color")
            row = box.row()
            row.prop(scene, "measureit_rf_border", text="Space")
            row.prop(scene, "measureit_rf_line", text="Width")


# -------------------------------------------------------------
# Defines button that adds a measure segment
#
# -------------------------------------------------------------
class MEASUREIT_OT_FontZoom(Operator):
    bl_idname = "measureit.fontzoom"
    bl_label = "Font size at current zoom"
    bl_description = ("+ / -: add or remove one point to the font size at every zoom level. "
                      "Reset: use the current zoom as reference and clear the offset")
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(items=(('PLUS', "+", ""),
                                ('MINUS', "-", ""),
                                ('RESET', "Reset", "")),
                         name="Action", default='PLUS')

    def execute(self, context):
        from .measureit_geometry import find_rv3d
        scene = context.scene
        rv3d = find_rv3d(context)
        if rv3d is None or rv3d.view_perspective == 'CAMERA' or rv3d.view_distance <= 0.0:
            self.report({'WARNING'}, "Use it from a 3D viewport, outside camera view")
            return {'CANCELLED'}
        if self.action == 'RESET' or scene.measureit_zoom_ref <= 0.0:
            scene.measureit_zoom_ref = rv3d.view_distance
            scene.measureit_zoom_ref_rot = tuple(rv3d.view_rotation)
        if self.action == 'RESET':
            scene.measureit_font_offset = 0
        elif self.action == 'PLUS':
            scene.measureit_font_offset += 1
        else:
            scene.measureit_font_offset -= 1
        if context.screen is not None:
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        return {'FINISHED'}


class MEASUREIT_OT_AddSegment(Operator):
    bl_idname = "measureit.addsegment"
    bl_label = "Add"
    bl_description = "Add dimensions for selected edges/vertices on every mesh in Multi-Object Edit Mode"

    # ------------------------------
    # Poll
    # ------------------------------
    @classmethod
    def poll(cls, context):
        o = context.object
        if o is None:
            return False
        else:
            if o.type == "MESH":
                if bpy.context.mode == 'EDIT_MESH':
                    return True
                else:
                    return False
            else:
                return False

    # ------------------------------
    # Execute button action
    # ------------------------------
    @multi_object_edit
    def execute(self, context):
        if context.area.type == 'VIEW_3D':
            # Add properties
            scene = context.scene
            mainobject = context.object
            mylist = get_smart_selected(mainobject)
            if len(mylist) < 2:  # if not selected linked vertex
                mylist = get_selected_vertex(mainobject)

            if len(mylist) >= 2:
                if not hasattr(mainobject, "MeasureGenerator") or len(mainobject.MeasureGenerator) == 0:
                    mainobject.MeasureGenerator.add()

                mp = mainobject.MeasureGenerator[0]
                for x in range(0, len(mylist) - 1, 2):
                    # -----------------------
                    # Only if not exist
                    # -----------------------
                    if exist_segment(mp, mylist[x], mylist[x + 1]) is False:
                        # Create all array elements
                        for cont in range(len(mp.measureit_segments) - 1, mp.measureit_num):
                            mp.measureit_segments.add()

                        # Set values
                        ms = mp.measureit_segments[mp.measureit_num]
                        ms.gltype = 1
                        ms.glpointa = mylist[x]
                        ms.glpointb = mylist[x + 1]
                        ms.glarrow_a = scene.measureit_glarrow_a
                        ms.glarrow_b = scene.measureit_glarrow_b
                        ms.glarrow_s = scene.measureit_glarrow_s
                        # color
                        ms.glcolor = scene.measureit_default_color
                        # dist
                        ms.glspace = scene.measureit_hint_space
                        # text
                        ms.gltxt = scene.measureit_gl_txt
                        ms.glfont_size = scene.measureit_font_size
                        ms.glfont_align = scene.measureit_font_align
                        ms.glfont_rotat = scene.measureit_font_rotation
                        # Sum group
                        ms.gltot = scene.measureit_sum
                        # Add index
                        mp.measureit_num += 1

                # redraw
                context.area.tag_redraw()
                return {'FINISHED'}
            else:
                self.report({'ERROR'},
                            "MeasureIt: Select at least two vertices for creating measure segment.")
                return {'FINISHED'}
        else:
            self.report({'WARNING'},
                        "View3D not found, cannot run operator")

        return {'CANCELLED'}


# -------------------------------------------------------------
# Defines button that adds an area measure
#
# -------------------------------------------------------------
class MEASUREIT_OT_AddArcChain(Operator):
    bl_idname = "measureit.addarcchain"
    bl_label = "Arc Chain"
    bl_description = "Measure selected edge chains on all edited meshes, or detect an arc from a seed edge/vertex"

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH' and context.mode == 'EDIT_MESH'

    @multi_object_edit
    def execute(self, context):
        obj = context.object
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        edges = [edge for edge in bm.edges if edge.select]
        selected_vertices = [v for v in bm.verts if v.select]

        # A single seed edge or vertex grows in both directions along the
        # boundary, stopping at sharp corners leading into straight sides.
        if len(edges) == 1 or (len(edges) == 0 and len(selected_vertices) == 1):
            def neighbors(vertex):
                boundary = [e for e in vertex.link_edges if len(e.link_faces) <= 1]
                return boundary if boundary else list(vertex.link_edges)

            def grow(previous, current):
                result = []
                visited = {previous.index, current.index}
                for _ in range(len(bm.verts)):
                    choices = []
                    for edge in neighbors(current):
                        nxt = edge.other_vert(current)
                        if nxt.index in visited:
                            continue
                        incoming = previous.co - current.co
                        outgoing = nxt.co - current.co
                        if incoming.length < 1e-10 or outgoing.length < 1e-10:
                            continue
                        turn = 180.0 - degrees(incoming.angle(outgoing))
                        choices.append((turn, nxt.index, nxt))
                    if not choices:
                        break
                    turn, _, nxt = min(choices, key=lambda item: (item[0], item[1]))
                    if turn > 40.0:
                        break
                    result.append(nxt)
                    visited.add(nxt.index)
                    previous, current = current, nxt
                return result

            if len(edges) == 1:
                first, second = edges[0].verts
            else:
                center = selected_vertices[0]
                candidates = [e.other_vert(center) for e in neighbors(center)]
                if len(candidates) != 2:
                    self.report({'ERROR'}, "Select an arc edge, or an interior vertex with two boundary neighbors")
                    return {'CANCELLED'}
                first, second = candidates
                # The central vertex belongs between both directions.
                seed_middle = center

            if len(edges) == 1:
                chain_verts = list(reversed(grow(second, first))) + [first, second] + grow(first, second)
            else:
                chain_verts = list(reversed(grow(seed_middle, first))) + [first, seed_middle, second] + grow(seed_middle, second)
            if len(chain_verts) < 3:
                self.report({'ERROR'}, "Could not detect an arc chain from this selection")
                return {'CANCELLED'}
            order = [v.index for v in chain_verts]
            # Do not change the user's selection until the explicit selection
            # toggle is enabled in the item's options.
        else:
            order = None

        if order is None:
            adjacency = {}
            for edge in edges:
                a, b = (v.index for v in edge.verts)
                adjacency.setdefault(a, []).append(b)
                adjacency.setdefault(b, []).append(a)
            ends = [i for i, adjacent in adjacency.items() if len(adjacent) == 1]
            if len(ends) != 2 or any(len(n) > 2 for n in adjacency.values()):
                self.report({'ERROR'}, "Select one connected open edge chain without branches")
                return {'CANCELLED'}
            order = [min(ends)]
            previous = None
            while True:
                candidates = [v for v in adjacency[order[-1]] if v != previous]
                if not candidates:
                    break
                previous = order[-1]
                order.append(candidates[0])
                if len(order) > len(adjacency):
                    break
            if len(order) != len(adjacency) or order[-1] not in ends:
                self.report({'ERROR'}, "Selected edges do not form a continuous chain")
                return {'CANCELLED'}
        if not hasattr(obj, "MeasureGenerator") or len(obj.MeasureGenerator) == 0:
            obj.MeasureGenerator.add()
        mp = obj.MeasureGenerator[0]
        ms = mp.measureit_segments.add()
        ms.gltype = 21
        for index in order:
            ms.chain_indices.add().glidx = index
        scene = context.scene
        ms.glcolor = scene.measureit_default_color
        ms.glfont_size = scene.measureit_font_size
        ms.glfont_align = scene.measureit_font_align
        ms.glfont_rotat = scene.measureit_font_rotation
        ms.gltxt = scene.measureit_gl_txt
        mp.measureit_num = len(mp.measureit_segments)
        if context.area is not None:
            context.area.tag_redraw()
        return {'FINISHED'}


class MEASUREIT_OT_AddArea(Operator):
    bl_idname = "measureit.addarea"
    bl_label = "Area"
    bl_description = "Add Area measurements for selected faces on all meshes in Multi-Object Edit Mode"

    # ------------------------------
    # Poll
    # ------------------------------
    @classmethod
    def poll(cls, context):
        o = context.object
        if o is None:
            return False
        else:
            if o.type == "MESH":
                if bpy.context.mode == 'EDIT_MESH':
                    return True
                else:
                    return False
            else:
                return False

    # ------------------------------
    # Execute button action
    # ------------------------------
    @multi_object_edit
    def execute(self, context):
        if context.area.type == 'VIEW_3D':
            # Add properties
            scene = context.scene
            mainobject = context.object
            mylist = get_selected_faces(mainobject)
            if len(mylist) >= 1:
                if not hasattr(mainobject, "MeasureGenerator") or len(mainobject.MeasureGenerator) == 0:
                    mainobject.MeasureGenerator.add()

                mp = mainobject.MeasureGenerator[0]
                mp.measureit_segments.add()
                ms = mp.measureit_segments[mp.measureit_num]
                ms.gltype = 20

                f = -1
                for face in mylist:
                    # Create array elements
                    ms.measureit_faces.add()
                    f += 1
                    # Set values
                    mf = ms.measureit_faces[f]
                    mf.glface = f
                    i = 0
                    for v in face:
                        mf.measureit_index.add()
                        mi = mf.measureit_index[i]
                        mi.glidx = v
                        i += 1

                # color
                rgb = scene.measureit_default_color
                ms.glcolor = (rgb[0], rgb[1], rgb[2], 0.4)
                # dist
                ms.glspace = scene.measureit_hint_space
                # text
                ms.gltxt = scene.measureit_gl_txt
                ms.glfont_size = scene.measureit_font_size
                ms.glfont_align = scene.measureit_font_align
                ms.glfont_rotat = scene.measureit_font_rotation
                # Sum group
                ms.gltot = scene.measureit_sum
                # Add index
                mp.measureit_num += 1
                # redraw
                context.area.tag_redraw()
                return {'FINISHED'}
            else:
                self.report({'ERROR'},
                            "MeasureIt: Select at least one face for creating area measure. ")
                return {'FINISHED'}
        else:
            self.report({'WARNING'},
                        "View3D not found, cannot run operator")

        return {'CANCELLED'}


# -------------------------------------------------------------
# Defines button that adds a measure segment to x/y/z origin
#
# -------------------------------------------------------------
class MEASUREIT_OT_AddSegmentOrto(Operator):
    bl_idname = "measureit.addsegmentorto"
    bl_label = "Add"
    bl_description = "Add axis distances for selected vertices on every mesh in Multi-Object Edit Mode"
    tag: IntProperty()

    # ------------------------------
    # Poll
    # ------------------------------
    @classmethod
    def poll(cls, context):
        o = context.object
        if o is None:
            return False
        else:
            if o.type == "MESH":
                if bpy.context.mode == 'EDIT_MESH':
                    return True
                else:
                    return False
            else:
                return False

    # ------------------------------
    # Execute button action
    # ------------------------------
    @multi_object_edit
    def execute(self, context):
        if context.area.type == 'VIEW_3D':
            # Add properties
            scene = context.scene
            mainobject = context.object
            mylist = get_smart_selected(mainobject)
            if len(mylist) < 1:  # if not selected linked vertex
                mylist = get_selected_vertex(mainobject)

            if len(mylist) >= 1:
                if not hasattr(mainobject, "MeasureGenerator") or len(mainobject.MeasureGenerator) == 0:
                    mainobject.MeasureGenerator.add()

                mp = mainobject.MeasureGenerator[0]
                for x in range(len(mylist)):
                    # -----------------------
                    # Only if not exist
                    # -----------------------
                    if exist_segment(mp, mylist[x], mylist[x], 12 + int(self.tag)) is False:
                        # Create all array elements
                        for cont in range(len(mp.measureit_segments) - 1, mp.measureit_num):
                            mp.measureit_segments.add()

                        # Set values
                        ms = mp.measureit_segments[mp.measureit_num]
                        ms.gltype = 12 + int(self.tag)
                        ms.glpointa = mylist[x]
                        ms.glpointb = mylist[x]
                        ms.glarrow_a = scene.measureit_glarrow_a
                        ms.glarrow_b = scene.measureit_glarrow_b
                        ms.glarrow_s = scene.measureit_glarrow_s
                        # color
                        ms.glcolor = scene.measureit_default_color
                        # dist
                        ms.glspace = scene.measureit_hint_space
                        # text
                        ms.gltxt = scene.measureit_gl_txt
                        ms.glfont_size = scene.measureit_font_size
                        ms.glfont_align = scene.measureit_font_align
                        ms.glfont_rotat = scene.measureit_font_rotation
                        # Sum group
                        ms.gltot = scene.measureit_sum
                        # Add index
                        mp.measureit_num += 1

                # redraw
                context.area.tag_redraw()
                return {'FINISHED'}
            else:
                self.report({'ERROR'},
                            "MeasureIt: Select at least one vertex for creating measure segment.")
                return {'FINISHED'}
        else:
            self.report({'WARNING'},
                        "View3D not found, cannot run operator")

        return {'CANCELLED'}


# -------------------------------------------------------------
# Defines button that adds an angle measure
#
# -------------------------------------------------------------
class MEASUREIT_OT_AddAngle(Operator):
    bl_idname = "measureit.addangle"
    bl_label = "Angle"
    bl_description = "Add an angle per edited mesh (select 3 vertices per mesh; 2nd is the angle vertex)"

    # ------------------------------
    # Poll
    # ------------------------------
    @classmethod
    def poll(cls, context):
        o = context.object
        if o is None:
            return False
        else:
            if o.type == "MESH":
                if bpy.context.mode == 'EDIT_MESH':
                    return True
                else:
                    return False
            else:
                return False

    # ------------------------------
    # Execute button action
    # ------------------------------
    @multi_object_edit
    def execute(self, context):
        if context.area.type == 'VIEW_3D':
            # Add properties
            scene = context.scene
            mainobject = context.object
            mylist = get_selected_vertex_history(mainobject)
            if len(mylist) == 3:
                if not hasattr(mainobject, "MeasureGenerator") or len(mainobject.MeasureGenerator) == 0:
                    mainobject.MeasureGenerator.add()

                mp = mainobject.MeasureGenerator[0]
                # -----------------------
                # Only if not exist
                # -----------------------
                if exist_segment(mp, mylist[0], mylist[1], 9, mylist[2]) is False:
                    # Create all array elements
                    for cont in range(len(mp.measureit_segments) - 1, mp.measureit_num):
                        mp.measureit_segments.add()

                    # Set values
                    ms = mp.measureit_segments[mp.measureit_num]
                    ms.gltype = 9
                    ms.glpointa = mylist[0]
                    ms.glpointb = mylist[1]
                    ms.glpointc = mylist[2]
                    # color
                    ms.glcolor = scene.measureit_default_color
                    # dist
                    ms.glspace = scene.measureit_hint_space
                    # text
                    ms.gltxt = scene.measureit_gl_txt
                    ms.glfont_size = scene.measureit_font_size
                    ms.glfont_align = scene.measureit_font_align
                    ms.glfont_rotat = scene.measureit_font_rotation
                    # Add index
                    mp.measureit_num += 1

                # redraw
                context.area.tag_redraw()
                return {'FINISHED'}
            else:
                self.report({'ERROR'},
                            "MeasureIt: Select three vertices for creating angle measure")
                return {'FINISHED'}
        else:
            self.report({'WARNING'},
                        "View3D not found, cannot run operator")

        return {'CANCELLED'}


# -------------------------------------------------------------
# Defines button that adds an arc measure
#
# -------------------------------------------------------------
class MEASUREIT_OT_AddArc(Operator):
    bl_idname = "measureit.addarc"
    bl_label = "Angle"
    bl_description = "Add an arc per edited mesh (select 3 vertices per mesh, 1st/3rd are arc ends)"

    # ------------------------------
    # Poll
    # ------------------------------
    @classmethod
    def poll(cls, context):
        o = context.object
        if o is None:
            return False
        else:
            if o.type == "MESH":
                if bpy.context.mode == 'EDIT_MESH':
                    return True
                else:
                    return False
            else:
                return False

    # ------------------------------
    # Execute button action
    # ------------------------------
    @multi_object_edit
    def execute(self, context):
        if context.area.type == 'VIEW_3D':
            # Add properties
            scene = context.scene
            mainobject = context.object
            mylist = get_selected_vertex_history(mainobject)
            if len(mylist) == 3:
                if not hasattr(mainobject, "MeasureGenerator") or len(mainobject.MeasureGenerator) == 0:
                    mainobject.MeasureGenerator.add()

                mp = mainobject.MeasureGenerator[0]
                # -----------------------
                # Only if not exist
                # -----------------------
                if exist_segment(mp, mylist[0], mylist[1], 11, mylist[2]) is False:
                    # Create all array elements
                    for cont in range(len(mp.measureit_segments) - 1, mp.measureit_num):
                        mp.measureit_segments.add()

                    # Set values
                    ms = mp.measureit_segments[mp.measureit_num]
                    ms.gltype = 11
                    ms.glpointa = mylist[0]
                    ms.glpointb = mylist[1]
                    ms.glpointc = mylist[2]
                    ms.glarrow_a = scene.measureit_glarrow_a
                    ms.glarrow_b = scene.measureit_glarrow_b
                    ms.glarrow_s = scene.measureit_glarrow_s
                    # color
                    ms.glcolor = scene.measureit_default_color
                    # dist
                    ms.glspace = scene.measureit_hint_space
                    # text
                    ms.gltxt = scene.measureit_gl_txt
                    ms.glfont_size = scene.measureit_font_size
                    ms.glfont_align = scene.measureit_font_align
                    ms.glfont_rotat = scene.measureit_font_rotation
                    # Add index
                    mp.measureit_num += 1

                # redraw
                context.area.tag_redraw()
                return {'FINISHED'}
            else:
                self.report({'ERROR'},
                            "MeasureIt: Select three vertices for creating arc measure")
                return {'FINISHED'}
        else:
            self.report({'WARNING'},
                        "View3D not found, cannot run operator")

        return {'CANCELLED'}


# -------------------------------------------------------------
# Defines button that adds a label segment
#
# -------------------------------------------------------------
class MEASUREIT_OT_AddLabel(Operator):
    bl_idname = "measureit.addlabel"
    bl_label = "Add"
    bl_description = "Add a label at the selected vertex on each mesh in Multi-Object Edit Mode"

    # ------------------------------
    # Poll
    # ------------------------------
    @classmethod
    def poll(cls, context):
        o = context.object
        if o is None:
            return False
        else:
            if o.type == "MESH":
                if bpy.context.mode == 'EDIT_MESH':
                    return True
                else:
                    return False
            else:
                return False

    # ------------------------------
    # Execute button action
    # ------------------------------
    @multi_object_edit
    def execute(self, context):
        if context.area.type == 'VIEW_3D':
            # Add properties
            scene = context.scene
            mainobject = context.object
            mylist = get_selected_vertex(mainobject)
            if len(mylist) == 1:
                if not hasattr(mainobject, "MeasureGenerator") or len(mainobject.MeasureGenerator) == 0:
                    mainobject.MeasureGenerator.add()

                mp = mainobject.MeasureGenerator[0]
                # -----------------------
                # Only if not exist
                # -----------------------
                if exist_segment(mp, mylist[0], mylist[0], 2) is False:  # Both equal
                    # Create all array elements
                    for cont in range(len(mp.measureit_segments) - 1, mp.measureit_num):
                        mp.measureit_segments.add()

                    # Set values
                    ms = mp.measureit_segments[mp.measureit_num]
                    ms.gltype = 2
                    ms.glpointa = mylist[0]
                    ms.glpointb = mylist[0]  # Equal
                    ms.glarrow_a = scene.measureit_glarrow_a
                    ms.glarrow_b = scene.measureit_glarrow_b
                    ms.glarrow_s = scene.measureit_glarrow_s
                    # color
                    ms.glcolor = scene.measureit_default_color
                    # dist
                    ms.glspace = scene.measureit_hint_space
                    # text
                    ms.gltxt = scene.measureit_gl_txt
                    ms.glfont_size = scene.measureit_font_size
                    ms.glfont_align = scene.measureit_font_align
                    ms.glfont_rotat = scene.measureit_font_rotation
                    # Add index
                    mp.measureit_num += 1

                # redraw
                context.area.tag_redraw()
                return {'FINISHED'}
            else:
                self.report({'ERROR'},
                            "MeasureIt: Select one vertex for creating measure label")
                return {'FINISHED'}
        else:
            self.report({'WARNING'},
                        "View3D not found, cannot run operator")

        return {'CANCELLED'}


# -------------------------------------------------------------
# Defines button that adds a link
#
# -------------------------------------------------------------
class MEASUREIT_OT_AddLink(Operator):
    bl_idname = "measureit.addlink"
    bl_label = "Add"
    bl_description = "(OBJECT mode only) Add a new measure between objects (select 2 " \
                     "objects and optionally 1 or 2 vertices)"

    # ------------------------------
    # Poll
    # ------------------------------
    @classmethod
    def poll(cls, context):
        o = context.object
        if o is None:
            return False
        else:
            if o.type == "MESH" or o.type == "EMPTY" or o.type == "CAMERA" or o.type == "LIGHT":
                if bpy.context.mode == 'OBJECT':
                    return True
                else:
                    return False
            else:
                return False

    # ------------------------------
    # Execute button action
    # ------------------------------
    def execute(self, context):
        if context.area.type == 'VIEW_3D':
            scene = context.scene
            mainobject = context.object
            # -------------------------------
            # Verify number of objects
            # -------------------------------
            if len(context.selected_objects) != 2:
                self.report({'ERROR'},
                            "MeasureIt: Select two objects only, and optionally 1 vertex or 2 vertices "
                            "(one of each object)")
                return {'FINISHED'}
            # Locate other object
            linkobject = None
            for o in context.selected_objects:
                if o.name != mainobject.name:
                    linkobject = o.name
            # Verify destination vertex
            lkobj = bpy.data.objects[linkobject]
            mylinkvertex = get_selected_vertex(lkobj)
            if len(mylinkvertex) > 1:
                self.report({'ERROR'},
                            "MeasureIt: The destination object has more than one vertex selected. "
                            "Select only 1 or none")
                return {'FINISHED'}
            # Verify origin vertex
            myobjvertex = get_selected_vertex(mainobject)
            if len(mylinkvertex) > 1:
                self.report({'ERROR'},
                            "MeasureIt: The active object has more than one vertex selected. Select only 1 or none")
                return {'FINISHED'}

            # -------------------------------
            # Add properties
            # -------------------------------
            flag = False
            if not hasattr(mainobject, "MeasureGenerator") or len(mainobject.MeasureGenerator) == 0:
                mainobject.MeasureGenerator.add()

            mp = mainobject.MeasureGenerator[0]

            # if exist_segment(mp, mylist[0], mylist[0], 3) is False:
            #     flag = True
            # Create all array elements
            for cont in range(len(mp.measureit_segments) - 1, mp.measureit_num):
                mp.measureit_segments.add()

            # Set values
            ms = mp.measureit_segments[mp.measureit_num]
            # -----------------------
            # Vertex to Vertex
            # -----------------------
            if len(myobjvertex) == 1 and len(mylinkvertex) == 1:
                ms.gltype = 3
                ms.glpointa = myobjvertex[0]
                ms.glpointb = mylinkvertex[0]
                flag = True
            # -----------------------
            # Vertex to Object
            # -----------------------
            if len(myobjvertex) == 1 and len(mylinkvertex) == 0:
                ms.gltype = 4
                ms.glpointa = myobjvertex[0]
                ms.glpointb = 0
                flag = True
            # -----------------------
            # Object to Vertex
            # -----------------------
            if len(myobjvertex) == 0 and len(mylinkvertex) == 1:
                ms.gltype = 5
                ms.glpointa = 0
                ms.glpointb = mylinkvertex[0]
                flag = True
            # -----------------------
            # Object to Object
            # -----------------------
            if len(myobjvertex) == 0 and len(mylinkvertex) == 0:
                ms.gltype = 8
                ms.glpointa = 0
                ms.glpointb = 0  # Equal
                flag = True

            # ------------------
            # only if created
            # ------------------
            if flag is True:
                ms.glarrow_a = scene.measureit_glarrow_a
                ms.glarrow_b = scene.measureit_glarrow_b
                ms.glarrow_s = scene.measureit_glarrow_s
                # color
                ms.glcolor = scene.measureit_default_color
                # dist
                ms.glspace = scene.measureit_hint_space
                # text
                ms.gltxt = scene.measureit_gl_txt
                ms.glfont_size = scene.measureit_font_size
                ms.glfont_align = scene.measureit_font_align
                ms.glfont_rotat = scene.measureit_font_rotation
                # link
                ms.gllink = linkobject
                # Add index
                mp.measureit_num += 1

                # -----------------------
                # Only if not exist
                # -----------------------
                # redraw
                context.area.tag_redraw()
                return {'FINISHED'}
        else:
            self.report({'WARNING'},
                        "View3D not found, cannot run operator")

        return {'CANCELLED'}


# -------------------------------------------------------------
# Defines button that adds an origin segment
#
# -------------------------------------------------------------
class MEASUREIT_OT_AddOrigin(Operator):
    bl_idname = "measureit.addorigin"
    bl_label = "Add"
    bl_description = "(OBJECT mode only) Add a new measure to origin (select object and optionally 1 vertex)"

    # ------------------------------
    # Poll
    # ------------------------------
    @classmethod
    def poll(cls, context):
        o = context.object
        if o is None:
            return False
        else:
            if o.type == "MESH" or o.type == "EMPTY" or o.type == "CAMERA" or o.type == "LIGHT":
                if bpy.context.mode == 'OBJECT':
                    return True
                else:
                    return False
            else:
                return False

    # ------------------------------
    # Execute button action
    # ------------------------------
    def execute(self, context):
        if context.area.type == 'VIEW_3D':
            # Add properties
            scene = context.scene
            mainobject = context.object
            mylist = get_selected_vertex(mainobject)
            if not hasattr(mainobject, "MeasureGenerator") or len(mainobject.MeasureGenerator) == 0:
                mainobject.MeasureGenerator.add()

            mp = mainobject.MeasureGenerator[0]
            # Create all array elements
            for cont in range(len(mp.measureit_segments) - 1, mp.measureit_num):
                mp.measureit_segments.add()

            # -----------------------
            # Set values
            # -----------------------
            ms = mp.measureit_segments[mp.measureit_num]
            flag = False
            if len(mylist) > 0:
                if len(mylist) == 1:
                    if exist_segment(mp, mylist[0], mylist[0], 6) is False:  # Both equal
                        flag = True
                        # Vertex to origin
                        ms.gltype = 6
                        ms.glpointa = mylist[0]
                        ms.glpointb = mylist[0]
                else:
                    self.report({'ERROR'},
                                "MeasureIt: Enter in EDITMODE and select one vertex only for creating "
                                "measure from vertex to origin")
                    return {'FINISHED'}
            else:
                # Object to origin
                if exist_segment(mp, 0, 0, 7) is False:  # Both equal
                    flag = True
                    ms.gltype = 7
                    ms.glpointa = 0
                    ms.glpointb = 0
            # ------------------
            # only if created
            # ------------------
            if flag is True:
                ms.glarrow_a = scene.measureit_glarrow_a
                ms.glarrow_b = scene.measureit_glarrow_b
                ms.glarrow_s = scene.measureit_glarrow_s
                # color
                ms.glcolor = scene.measureit_default_color
                # dist
                ms.glspace = scene.measureit_hint_space
                # text
                ms.gltxt = scene.measureit_gl_txt
                ms.glfont_size = scene.measureit_font_size
                ms.glfont_align = scene.measureit_font_align
                ms.glfont_rotat = scene.measureit_font_rotation
                # Add index
                mp.measureit_num += 1

            # redraw
            context.area.tag_redraw()

            return {'FINISHED'}
        else:
            self.report({'WARNING'},
                        "View3D not found, cannot run operator")

        return {'CANCELLED'}


# -------------------------------------------------------------
# Defines button that deletes a measure segment
#
# -------------------------------------------------------------
class MEASUREIT_OT_DeleteSegment(Operator):
    bl_idname = "measureit.deletesegment"
    bl_label = "Delete"
    bl_description = "Delete a measure"
    tag: IntProperty()

    # ------------------------------
    # Execute button action
    # ------------------------------
    def execute(self, context):
        if context.area.type == 'VIEW_3D':
            # Add properties
            mainobject = context.object
            mp = mainobject.MeasureGenerator[0]
            ms = mp.measureit_segments[self.tag]
            ms.glfree = True
            # Delete element
            mp.measureit_segments.remove(self.tag)
            mp.measureit_num -= 1
            # redraw
            context.area.tag_redraw()
            return {'FINISHED'}
        else:
            self.report({'WARNING'},
                        "View3D not found, cannot run operator")

        return {'CANCELLED'}


# -------------------------------------------------------------
# Defines button that deletes all measure segments
#
# -------------------------------------------------------------
class MEASUREIT_OT_DeleteAllSegment(Operator):
    bl_idname = "measureit.deleteallsegment"
    bl_label = "Delete"
    bl_description = "Delete all measures (it cannot be undone)"
    tag: IntProperty()

    # ------------------------------
    # Execute button action
    # ------------------------------
    def execute(self, context):
        if context.area.type == 'VIEW_3D':
            # Add properties
            mainobject = context.object
            mp = mainobject.MeasureGenerator[0]

            while len(mp.measureit_segments) > 0:
                mp.measureit_segments.remove(0)

            # reset size
            mp.measureit_num = len(mp.measureit_segments)
            # redraw
            context.area.tag_redraw()
            return {'FINISHED'}
        else:
            self.report({'WARNING'},
                        "View3D not found, cannot run operator")

        return {'CANCELLED'}


# -------------------------------------------------------------
# Defines button that deletes all measure segment sums
#
# -------------------------------------------------------------
class MEASUREIT_OT_DeleteAllSum(Operator):
    bl_idname = "measureit.deleteallsum"
    bl_label = "Delete"
    bl_description = "Delete all sum groups"
    tag: IntProperty()

    # ------------------------------
    # Execute button action
    # ------------------------------
    # noinspection PyMethodMayBeStatic
    def execute(self, context):
        if context.object is not None:
            if hasattr(context.object, "MeasureGenerator") and len(context.object.MeasureGenerator) > 0:
                mp = context.object.MeasureGenerator[0]
                for idx in range(mp.measureit_num):
                    ms = mp.measureit_segments[idx]
                    ms.gltot = '99'

            return {'FINISHED'}


# -------------------------------------------------------------
# Defines button that expands all measure segments
#
# -------------------------------------------------------------
class MEASUREIT_OT_ExpandAllSegment(Operator):
    bl_idname = "measureit.expandallsegment"
    bl_label = "Expand"
    bl_description = "Expand all measure properties"
    tag: IntProperty()

    # ------------------------------
    # Execute button action
    # ------------------------------
    def execute(self, context):
        if context.area.type == 'VIEW_3D':
            # Add properties
            mainobject = context.object
            mp = mainobject.MeasureGenerator[0]

            for i in mp.measureit_segments:
                i.gladvance = True

            return {'FINISHED'}
        else:
            self.report({'WARNING'},
                        "View3D not found, cannot run operator")

        return {'CANCELLED'}


# -------------------------------------------------------------
# Defines button that collapses all measure segments
#
# -------------------------------------------------------------
class MEASUREIT_OT_CollapseAllSegment(Operator):
    bl_idname = "measureit.collapseallsegment"
    bl_label = "Collapse"
    bl_description = "Collapses all measure properties"
    tag: IntProperty()

    # ------------------------------
    # Execute button action
    # ------------------------------
    def execute(self, context):
        if context.area.type == 'VIEW_3D':
            # Add properties
            mainobject = context.object
            mp = mainobject.MeasureGenerator[0]

            for i in mp.measureit_segments:
                i.gladvance = False

            return {'FINISHED'}
        else:
            self.report({'WARNING'},
                        "View3D not found, cannot run operator")

        return {'CANCELLED'}


# -------------------------------------------------------------
# Defines button for render option
#
# -------------------------------------------------------------
class MEASUREIT_OT_RenderSegment(Operator):
    bl_idname = "measureit.rendersegment"
    bl_label = "Render"
    bl_description = "Create a render image with measures. Use UV/Image editor to view image generated"
    tag: IntProperty()

    # ------------------------------
    # Execute button action
    # ------------------------------
    # noinspection PyMethodMayBeStatic,PyUnusedLocal
    def execute(self, context):
        scene = context.scene
        msg = "New image created with measures. Open it in UV/image editor"
        camera_msg = "Unable to render. No camera found"
        # -----------------------------
        # Check camera
        # -----------------------------
        if scene.camera is None:
            self.report({'ERROR'}, camera_msg)
            return {'FINISHED'}
        # -----------------------------
        # Frame render
        # -----------------------------
        if scene.measureit_render_type == "1":
            # noinspection PyBroadException
            if render_main(self, context) is True:
                self.report({'INFO'}, msg)
        # -----------------------------
        # Animation
        # -----------------------------
        if scene.measureit_render_type == "2":
            oldframe = scene.frame_current
            flag = False
            # loop frames
            for frm in range(scene.frame_start, scene.frame_end + 1):
                scene.frame_set(frm)
                print("MeasureIt: Rendering frame %04d" % frm)
                flag = render_main(self, context, True)
                if flag is False:
                    break

            scene.frame_current = oldframe
            if flag is True:
                self.report({'INFO'}, msg)

        return {'FINISHED'}

    # ---------------------
    # Set cameraView
    # ---------------------
    # noinspection PyMethodMayBeStatic
    def set_camera_view(self):
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces[0].region_3d.view_perspective = 'CAMERA'

    # -------------------------------------
    # Set only render status
    # -------------------------------------
    # noinspection PyMethodMayBeStatic
    def set_only_render(self, status):
        screen = bpy.context.screen

        v3d = False
        s = None
        # get spaceview_3d in current screen
        for a in screen.areas:
            if a.type == 'VIEW_3D':
                for s in a.spaces:
                    if s.type == 'VIEW_3D':
                        v3d = s
                        break

        if v3d is not False:
            s.show_only_render = status


# -------------------------------------------------------------
# Defines a new note
#
# -------------------------------------------------------------
class MEASUREIT_OT_AddNote(Operator):
    bl_idname = "measureit.addnote"
    bl_label = "Note"
    bl_description = "(OBJECT mode only) Add a new annotation"
    tag: IntProperty()

    # ------------------------------
    # Poll
    # ------------------------------
    # noinspection PyUnusedLocal
    @classmethod
    def poll(cls, context):
        if bpy.context.mode == 'OBJECT':
            return True
        else:
            return False

    # ------------------------------
    # Execute button action
    # ------------------------------
    def execute(self, context):
        if context.area.type == 'VIEW_3D':
            bpy.ops.object.empty_add(type='PLAIN_AXES')
            myempty = bpy.data.objects[bpy.context.active_object.name]
            myempty.location = bpy.context.scene.cursor.location
            myempty.empty_display_size = 0.01
            myempty.name = "Annotation"
            # Add properties
            scene = context.scene
            mainobject = myempty
            if not hasattr(mainobject, "MeasureGenerator") or len(mainobject.MeasureGenerator) == 0:
                mainobject.MeasureGenerator.add()

            mp = mainobject.MeasureGenerator[0]
            # Create all array elements
            for cont in range(len(mp.measureit_segments) - 1, mp.measureit_num):
                mp.measureit_segments.add()

            # Set values
            ms = mp.measureit_segments[mp.measureit_num]
            ms.gltype = 10
            ms.glpointa = 0
            ms.glpointb = 0  # Equal
            # color
            ms.glcolor = scene.measureit_default_color
            # dist
            ms.glspace = scene.measureit_hint_space
            # text
            ms.gltxt = scene.measureit_gl_txt
            ms.glfont_size = scene.measureit_font_size
            ms.glfont_align = scene.measureit_font_align
            ms.glfont_rotat = scene.measureit_font_rotation
            # Add index
            mp.measureit_num += 1

            # redraw
            context.area.tag_redraw()
            return {'FINISHED'}
        else:
            self.report({'WARNING'},
                        "View3D not found, cannot run operator")

        return {'CANCELLED'}


# -------------------------------------------------------------
# Defines button that enables/disables the tip display
#
# -------------------------------------------------------------
class MEASUREIT_OT_RunHintDisplay(Operator):
    bl_idname = "measureit.runopengl"
    bl_label = "Display hint data manager"
    bl_description = "Main control for enabling or disabling the display of measurements in the viewport"

    _handle = None  # keep function handler

    # ----------------------------------
    # Enable gl drawing adding handler
    # ----------------------------------
    @staticmethod
    def handle_add(self, context):
        if MEASUREIT_OT_RunHintDisplay._handle is None:
            MEASUREIT_OT_RunHintDisplay._handle = SpaceView3D.draw_handler_add(draw_callback_px, (self, context),
                                                                        'WINDOW',
                                                                        'POST_PIXEL')
            context.window_manager.measureit_run_opengl = True

    # ------------------------------------
    # Disable gl drawing removing handler
    # ------------------------------------
    # noinspection PyUnusedLocal
    @staticmethod
    def handle_remove(self, context):
        if MEASUREIT_OT_RunHintDisplay._handle is not None:
            SpaceView3D.draw_handler_remove(MEASUREIT_OT_RunHintDisplay._handle, 'WINDOW')
        MEASUREIT_OT_RunHintDisplay._handle = None
        context.window_manager.measureit_run_opengl = False

    # ------------------------------
    # Execute button action
    # ------------------------------
    def execute(self, context):
        if context.area.type == 'VIEW_3D':
            if context.window_manager.measureit_run_opengl is False:
                self.handle_add(self, context)
                context.area.tag_redraw()
            else:
                self.handle_remove(self, context)
                context.area.tag_redraw()

            return {'FINISHED'}
        else:
            self.report({'WARNING'},
                        "View3D not found, cannot run operator")

        return {'CANCELLED'}


# -------------------------------------------------------------
# Handle all draw routines (OpenGL main entry point)
#
# -------------------------------------------------------------
def draw_main(context):
    region = bpy.context.region
    # Detect if Quadview to get drawing area
    if not context.space_data.region_quadviews:
        rv3d = bpy.context.space_data.region_3d
    else:
        # verify area
        if context.area.type != 'VIEW_3D' or context.space_data.type != 'VIEW_3D':
            return
        i = -1
        for region in context.area.regions:
            if region.type == 'WINDOW':
                i += 1
                if context.region.id == region.id:
                    break
        else:
            return

        rv3d = context.space_data.region_quadviews[i]

    scene = bpy.context.scene

    # Display selected or all
    if scene.measureit_gl_ghost is False:
        objlist = context.selected_objects
    else:
        objlist = context.view_layer.objects

    # Enable drawing
    gpu.state.blend_set('ALPHA')
    # ---------------------------------------
    # Generate all OpenGL calls for measures
    # ---------------------------------------
    for myobj in objlist:
        if myobj.visible_get() is True:
            if hasattr(myobj, "MeasureGenerator") and len(myobj.MeasureGenerator) > 0:
                op = myobj.MeasureGenerator[0]
                draw_segments(context, myobj, op, region, rv3d)

    # ---------------------------------------
    # Generate all OpenGL calls for debug
    # ---------------------------------------
    if scene.measureit_debug is True:
        selobj = bpy.context.selected_objects
        for myobj in selobj:
            if scene.measureit_debug_objects is True:
                draw_object(context, myobj, region, rv3d)
            elif scene.measureit_debug_object_loc is True:
                draw_object(context, myobj, region, rv3d)
            if scene.measureit_debug_vertices is True:
                draw_vertices(context, myobj, region, rv3d)
            elif scene.measureit_debug_vert_loc is True:
                draw_vertices(context, myobj, region, rv3d)
            if scene.measureit_debug_edges is True:
                draw_edges(context, myobj, region, rv3d)
            if scene.measureit_debug_faces is True or scene.measureit_debug_normals is True:
                draw_faces(context, myobj, region, rv3d)

    # -----------------------
    # restore defaults
    # -----------------------
    gpu.state.blend_set('NONE')


# -------------------------------------------------------------
# Handler for drawing OpenGl
# -------------------------------------------------------------
# noinspection PyUnusedLocal
def draw_callback_px(self, context):
    # MeasureIt draws through a persistent 3D viewport handler.  Blender 5.2
    # can reuse GPU state after an interrupted handler, so always return the
    # blend pipeline to Blender's default even if a malformed measurement or
    # another add-on raises while MeasureIt is drawing.
    try:
        draw_main(context)
    finally:
        gpu.state.blend_set('NONE')


# -------------------------------------------------------------
# Check if the segment already exist
#
# -------------------------------------------------------------
def exist_segment(mp, pointa, pointb, typ=1, pointc=None):
    #  for ms in mp.measureit_segments[mp.measureit_num]
    for ms in mp.measureit_segments:
        if ms.gltype == typ and ms.glfree is False:
            if typ != 9:
                if ms.glpointa == pointa and ms.glpointb == pointb:
                    return True
                if ms.glpointa == pointb and ms.glpointb == pointa:
                    return True
            else:
                if ms.glpointa == pointa and ms.glpointb == pointb and ms.glpointc == pointc:
                    return True

    return False


# -------------------------------------------------------------
# Get vertex selected
# -------------------------------------------------------------
def get_selected_vertex(myobject):
    mylist = []
    # if not mesh, no vertex
    if myobject.type != "MESH":
        return mylist
    # --------------------
    # meshes
    # --------------------
    oldobj = bpy.context.object
    bpy.context.view_layer.objects.active = myobject
    flag = False
    if myobject.mode != 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')
        flag = True

    bm = from_edit_mesh(myobject.data)
    tv = len(bm.verts)
    for v in bm.verts:
        if v.select:
            mylist.append(v.index)

    if flag is True:
        bpy.ops.object.editmode_toggle()
    # Back context object
    bpy.context.view_layer.objects.active = oldobj

    # if select all vertices, then use origin
    if tv == len(mylist):
        return []

    return mylist


# -------------------------------------------------------------
# Get vertex selected
# -------------------------------------------------------------
def get_selected_vertex_history(myobject):
    mylist = []
    # if not mesh, no vertex
    if myobject.type != "MESH":
        return mylist
    # --------------------
    # meshes
    # --------------------
    oldobj = bpy.context.object
    bpy.context.view_layer.objects.active = myobject
    flag = False
    if myobject.mode != 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')
        flag = True

    bm = from_edit_mesh(myobject.data)
    for v in bm.select_history:
        mylist.append(v.index)

    if flag is True:
        bpy.ops.object.editmode_toggle()
    # Back context object
    bpy.context.view_layer.objects.active = oldobj

    return mylist


# -------------------------------------------------------------
# Get vertex selected segments
# -------------------------------------------------------------
def get_smart_selected(myobject):
    mylist = []
    # if not mesh, no vertex
    if myobject.type != "MESH":
        return mylist
    # --------------------
    # meshes
    # --------------------
    oldobj = bpy.context.object
    bpy.context.view_layer.objects.active = myobject
    flag = False
    if myobject.mode != 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')
        flag = True

    bm = from_edit_mesh(myobject.data)
    for e in bm.edges:
        if e.select is True:
            mylist.append(e.verts[0].index)
            mylist.append(e.verts[1].index)

    if flag is True:
        bpy.ops.object.editmode_toggle()
    # Back context object
    bpy.context.view_layer.objects.active = oldobj

    return mylist


# -------------------------------------------------------------
# Get vertex selected faces
# -------------------------------------------------------------
def get_selected_faces(myobject):
    mylist = []
    # if not mesh, no vertex
    if myobject.type != "MESH":
        return mylist
    # --------------------
    # meshes
    # --------------------
    oldobj = bpy.context.object
    bpy.context.view_layer.objects.active = myobject
    flag = False
    if myobject.mode != 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')
        flag = True

    bm = from_edit_mesh(myobject.data)
    for e in bm.faces:
        myfaces = []
        if e.select is True:
            for i in range(len(e.verts)):
                myfaces.append(e.verts[i].index)

            mylist.extend([myfaces])

    if flag is True:
        bpy.ops.object.editmode_toggle()
    # Back context object
    bpy.context.view_layer.objects.active = oldobj

    return mylist
