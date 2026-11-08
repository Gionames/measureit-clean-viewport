# MeasureIt Clean Viewport

A small compatibility variant of the Blender add-on **MeasureIt** (Blender 5.0+).

- Original project: https://projects.blender.org/extensions/measureit
- Original author: Antonio Vazquez (antonioya), copyright 2024. GPL-3.0-or-later, and so is this variant.
- Based on upstream MeasureIt 1.8.4 (commit `c7b582c`, "Fix #5: Change API for Blender 5.0"). The full upstream history is preserved in this repository.

## Changes compared to upstream

- **Opaque-safe area labels (1.8.5)** - area measures no longer draw the translucent filled polygon over the measured mesh. With *Show* enabled on many overlapping objects, those fills made the models look almost transparent. The area is still calculated and its label is still drawn.
- **GPU state reset (1.8.5)** - the viewport draw handler now always resets the blend state (`gpu.state.blend_set('NONE')`) in a `try/finally`, even if drawing raises an exception.
- **Text Only option (1.9.0)** - a scene toggle in the MeasureIt panel that shows only measurement values and names, without lines or arrows. Off by default.

The add-on id is `measureit_clean_viewport`, different from upstream's `measureit`, so both can be installed side by side (do not enable both at the same time).

## Installation

1. Download `measureit_clean_viewport-1.9.0.zip` from the Releases page.
2. In Blender: Edit > Preferences > Get Extensions > top-right menu > *Install from Disk...* and pick the zip.

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