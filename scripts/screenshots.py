"""Regenerate the README screenshots (docs/screenshot-light.png / -dark.png).

Drives the real GUI with a demo playlist mid-download -- no network -- and
captures the window. Needs a display (on Linux: `xvfb-run -a python
scripts/screenshots.py`).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw, ImageFilter, ImageGrab  # noqa: E402

from melograb import env, gui, pipeline  # noqa: E402
from melograb.track import Collection, Track  # noqa: E402

DEMO = [
    ("Men I Trust", "Husk", "Oncle Jazz", 215),
    ("Mac DeMarco", "For the First Time", "This Old Dog", 179),
    ("Clairo", "Bags", "Immunity", 260),
    ("beabadoobee", "Coffee", "Patched Up", 129),
    ("Steve Lacy", "Dark Red", "Steve Lacy's Demo", 173),
    ("Cuco", "Lo Que Siento", "Wannabewithu", 226),
    ("boy pablo", "Everytime", "Roy Pablo", 162),
    ("Faye Webster", "Kingston", "Atlanta Millionaires Club", 197),
    ("Wallows", "Are You Bored Yet?", "Nothing Happens", 178),
    ("Dijon", "coogie", "Absolutely", 194),
    ("PinkPantheress", "Mosquito", "Fancy That", 157),
]


def cover(size=56):
    """A soft gradient stands in for playlist art (screenshots run offline)."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)
            px[x, y] = (int(244 - 60 * t), int(170 - 40 * t), int(140 + 80 * t))
    rgba = img.convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=10, fill=255)
    rgba.putalpha(mask)
    return rgba


def frame(shot: Image.Image, radius=14, shadow=28) -> Image.Image:
    """Rounded corners + drop shadow on a transparent canvas."""
    w, h = shot.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    canvas = Image.new("RGBA", (w + shadow * 2, h + shadow * 2), (0, 0, 0, 0))
    shade = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shade).rounded_rectangle(
        [shadow, shadow + 6, shadow + w, shadow + h + 6], radius=radius, fill=(0, 0, 0, 70)
    )
    canvas = Image.alpha_composite(canvas, shade.filter(ImageFilter.GaussianBlur(shadow / 2)))
    canvas.paste(shot.convert("RGBA"), (shadow, shadow), mask)
    return canvas


def capture(theme: str, path: str) -> None:
    gui.SETTINGS_PATH = os.path.join(os.path.dirname(path), f".settings-{theme}.json")
    env.environment_warnings = lambda: []
    env.ytdlp_update_warning = lambda latest=None: ""
    env.latest_app_release = lambda repo: {}
    gui._system_prefers_dark = lambda: theme == "dark"

    tracks = [Track(t, a, album=al, duration_ms=s * 1000, playlist="Bedroom Pop", position=i + 1)
              for i, (a, t, al, s) in enumerate(DEMO)]
    app = gui.MelograbApp()
    app.geometry("900x720")
    app.output_var.set("~/Music/melograb")
    app._load_tracks(Collection("playlist", "Bedroom Pop", "", tracks))
    app.cover_photo = gui.ImageTk.PhotoImage(cover())
    app.cover_label.configure(image=app.cover_photo)

    # Mid-download: six done, two in flight, the rest queued.
    app.run_indices = list(range(len(tracks)))
    for i, t in enumerate(tracks):
        if i < 6:
            app.results[i] = pipeline.Result(t, path=f"/x/{i}.mp3", status="done", video_id="x",
                                             match_title=f"{t.artist} - {t.title}")
            app._set_row(i, "done")
        else:
            app._set_row(i, "downloading" if i < 8 else "queued")
    app.progress.config(maximum=len(tracks), value=6)
    app.progress_pct.config(text="54%")
    app.status_var.set(f"Downloading 6 / {len(tracks)}…")
    app.download_btn.config(text="Cancel")
    app.tree.selection_set("0")
    app._on_select()

    def grab():
        app.update()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        bbox = (x, y, x + app.winfo_width(), y + app.winfo_height())
        display = os.environ.get("DISPLAY") if sys.platform.startswith("linux") else None
        shot = ImageGrab.grab(bbox=bbox, xdisplay=display) if display else ImageGrab.grab(bbox=bbox)
        frame(shot).save(path)
        print("wrote", path)
        app.destroy()

    app.after(800, grab)
    app.mainloop()
    try:
        os.remove(gui.SETTINGS_PATH)
    except OSError:
        pass


if __name__ == "__main__":
    out = os.path.join(ROOT, "docs")
    os.makedirs(out, exist_ok=True)
    for theme in ("light", "dark"):
        capture(theme, os.path.join(out, f"screenshot-{theme}.png"))
