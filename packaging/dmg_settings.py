# dmgbuild settings for melograb's installer disk image.
#
#   dmgbuild -s packaging/dmg_settings.py -D app=dist/melograb.app \
#            -D background=build/dmg/background.png "melograb" out.dmg
#
# dmgbuild runs this file with `defines` (the -D values) in scope. The icon
# positions must match packaging/make_dmg_background.py.
import os.path

app = defines.get("app", "dist/melograb.app")  # noqa: F821
app_name = os.path.basename(app)

format = "ULFO"  # LZFSE-compressed, read-only (macOS 10.11+)
filesystem = "HFS+"
files = [app]
symlinks = {"Applications": "/Applications"}
hide_extensions = [app_name]

icon = defines.get("icon") or None  # noqa: F821 - the mounted volume's icon
background = defines.get("background") or "builtin-arrow"  # noqa: F821 - @2x picked up automatically

window_rect = ((200, 140), (660, 420))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
show_icon_preview = False
icon_size = 128
text_size = 13
icon_locations = {
    app_name: (170, 215),
    "Applications": (490, 215),
}
