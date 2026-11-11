# MeasureIt Clean Viewport

A small compatibility variant of the Blender add-on **MeasureIt** (Blender 5.0+).

- Original project: https://projects.blender.org/extensions/measureit
- Original author: Antonio Vazquez (antonioya), copyright 2024. GPL-3.0-or-later, and so is this variant.
- Based on upstream MeasureIt 1.8.4 (commit `c7b582c`, "Fix #5: Change API for Blender 5.0"). The full upstream history is preserved in this repository.

## Changes compared to upstream

- **Opaque-safe area labels (1.8.5)** - area measures no longer draw the translucent filled polygon over the measured mesh. With *Show* enabled on many overlapping objects, those fills made the models look almost transparent. The area is still calculated and its label is still drawn.
- **GPU state reset (1.8.5)** - the viewport draw handler now always resets the blend state (`gpu.state.blend_set('NONE')`) in a `try/finally`, even if drawing raises an exception.
- **Text Only option (1.9.0)** - a scene toggle in the MeasureIt panel that shows only measurement values and names, without lines or arrows. Off by default.
- **Zoom-proportional viewport text (1.9.x)** - text size, lines, arrows and distances scale with the viewport zoom. The `-` / `+` buttons change a font scale (x1.1 per step) and *Reset zoom reference* sets the current zoom as the reference. The X/Y text offsets follow the 3D scene and rotate with the view.
- **Text placement (1.9.x)** - measure text sits on the outer side of its line with a small gap, for Left/Center/Right alignment and for dimensions, labels, angles, arcs, annotations and areas. Area text is centered on the area.
- **New defaults (1.9.x)** - white text color, Center alignment, distance 0.05.
- **Arc chain tool and text options (1.9.27)** - measures a chain of edges with estimated radius and angle (selection of the included edges, connector detection, start/end exclusion), line spacing for multi-line text and a name field. Developed with ChatGPT.
- **Collection-based area totals (1.9.28)** - sum visible Area measurements from separate Room and Wall collections, respecting the current View Layer. Expand the details to see included and excluded measurements.
- **Highlight counted areas (1.9.30)** - temporarily dim the viewport and draw thick green perimeters labeled with their Outliner object names. Selection and editing are blocked while inspecting; Esc exits, and zoom/pan remain available.

The add-on id is `measureit_clean_viewport`, different from upstream's `measureit`, so both can be installed side by side (do not enable both at the same time).

## Installation

1. Download the latest `measureit_clean_viewport-<version>.zip` from the [Releases page](https://github.com/Gionames/measureit-clean-viewport/releases/latest).
2. In Blender: Edit > Preferences > Get Extensions > top-right menu > *Install from Disk...* and pick the zip. Fully restart Blender after updating.

The sources are in [`source/`](source/).

## Status

Personal variant, provided as-is with no warranty. Not affiliated with or endorsed by the original author.

## License

GPL-3.0-or-later - see [LICENSE](LICENSE).

---

## Upstream README

# MeasureIt Add-on

This add-on was part of [Blender 4.1 bundled add-ons](https://docs.blender.org/manual/en/4.1/addons/). This is now available as an extension on the [Extensions platform](https://extensions.blender.org/add-ons/measureit).

To build a new version of the extension:
* `<path_to_blender> -c extension build --source-dir=./source`

For more information about building extensions refer to the [documentation](https://docs.blender.org/manual/en/4.4/advanced/extensions/getting_started.html).

---

This add-on is offered as it is and maintaned by the community, no support expected.
