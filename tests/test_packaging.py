import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_dmg_background_renders(tmp_path):
    subprocess.run([sys.executable, os.path.join(ROOT, "packaging", "make_dmg_background.py"), str(tmp_path),
                    "--unsigned"], check=True, capture_output=True)
    from PIL import Image

    assert Image.open(tmp_path / "background.png").size == (660, 420)
    assert Image.open(tmp_path / "background@2x.png").size == (1320, 840)


def test_dmg_settings_layout_matches_background():
    settings = {"defines": {"app": "dist/melograb.app", "background": "bg.png", "icon": "i.icns"}}
    with open(os.path.join(ROOT, "packaging", "dmg_settings.py")) as f:
        exec(compile(f.read(), "dmg_settings.py", "exec"), settings)  # noqa: S102 - our own file
    sys.path.insert(0, os.path.join(ROOT, "packaging"))
    import make_dmg_background as bg

    assert settings["icon_locations"] == {"melograb.app": (bg.APP_X, bg.ICON_Y), "Applications": (bg.APPS_X, bg.ICON_Y)}
    assert settings["window_rect"][1] == (bg.WIDTH, bg.HEIGHT)
    assert settings["files"] == ["dist/melograb.app"] and settings["symlinks"] == {"Applications": "/Applications"}


def test_shell_scripts_parse():
    for script in ("packaging/build_macos.sh", "packaging/fetch_tools.sh", "scripts/build_macos_app.sh"):
        subprocess.run(["bash", "-n", os.path.join(ROOT, script)], check=True)
