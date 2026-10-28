"""Minimalist desktop GUI for melograb: paste Spotify / YouTube Music links
(or type a song), preview the tracklist, then download + tag everything
locally -- grouped into folders, with synced lyrics embedded.

No login, no API key, no Premium -- reads the same public page data
Spotify serves to any logged-out visitor (see public_source.py).
"""
from __future__ import annotations

import io
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, simpledialog, ttk

import requests

from . import __version__, downloader, env, library, pipeline, public_source, sources, ytmusic_source
from . import template as templates
from .track import Track

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:  # Pillow is optional -- cover art just won't render
    Image = ImageDraw = ImageTk = None

log = logging.getLogger(__name__)

DEFAULT_OUTPUT = os.path.join(os.path.expanduser("~"), "Music", "melograb")
SETTINGS_PATH = os.path.join(env.config_dir(), "settings.json")
IS_MAC = sys.platform == "darwin"


def _assets_dir() -> str:
    # PyInstaller unpacks bundled data files under sys._MEIPASS at runtime.
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets")


def _load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_settings(settings: dict) -> None:
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        log.info("couldn't save settings to %s", SETTINGS_PATH)


def _open_path(path: str) -> None:
    if IS_MAC:
        subprocess.run(["open", path], check=False)
    elif sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", path], check=False)


def _reveal(path: str) -> None:
    """Show `path` selected in Finder (or just open its folder elsewhere)."""
    if IS_MAC and os.path.exists(path):
        subprocess.run(["open", "-R", path], check=False)
    else:
        _open_path(os.path.dirname(path))


def _notify(title: str, message: str) -> None:
    try:
        if IS_MAC:
            script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
            subprocess.run(["osascript", "-e", script], check=False, timeout=5)
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _system_prefers_dark() -> bool:
    if not IS_MAC:
        return False
    try:
        out = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True, timeout=2
        )
        return "dark" in out.stdout.lower()
    except (OSError, subprocess.TimeoutExpired):
        return False


# ---- minimal, neutral palettes ----
LIGHT = {
    "bg": "#fafafa", "surface": "#ffffff", "stripe": "#f7f7f8", "border": "#e4e4e7", "hover": "#f0f0f0",
    "selected": "#e4e4e7", "text": "#18181b", "subtext": "#71717a", "dim": "#a1a1aa",
    "accent": "#18181b", "accent_text": "#ffffff", "accent_hover": "#3f3f46", "accent_disabled": "#d4d4d8",
    "done": "#16a34a", "failed": "#dc2626", "active": "#2563eb",
    "warn_bg": "#fef3c7", "warn_text": "#92400e", "placeholder": (228, 228, 231, 255),
}
DARK = {
    "bg": "#18181b", "surface": "#1f1f23", "stripe": "#232328", "border": "#3f3f46", "hover": "#2a2a30",
    "selected": "#34343b", "text": "#f4f4f5", "subtext": "#a1a1aa", "dim": "#71717a",
    "accent": "#f4f4f5", "accent_text": "#18181b", "accent_hover": "#d4d4d8", "accent_disabled": "#3f3f46",
    "done": "#4ade80", "failed": "#f87171", "active": "#60a5fa",
    "warn_bg": "#422006", "warn_text": "#fcd34d", "placeholder": (63, 63, 70, 255),
}
FONT = ("SF Pro Text", 12)
FONT_SMALL = ("SF Pro Text", 11)
FONT_MONO = ("SF Mono", 11)

STATUS_LABELS = {
    "queued": "queued",
    "downloading": "downloading…",
    "done": "done",
    "skipped": "already here",
    "duplicate": "duplicate",
    "failed": "failed",
    "cancelled": "cancelled",
}
CHECKED, UNCHECKED = "☑", "☐"
CUSTOM_LAYOUT = "Custom"
SAMPLE_TRACK = Track(
    title="Husk", artist="Men I Trust", album="Oncle Jazz", track_number=7, release_date="2019-11-08",
    playlist="My Playlist", position=1,
)


class MelograbApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("melograb")
        self.geometry("900x780")
        self.minsize(760, 680)

        self.settings = _load_settings()
        self.c = DARK if self._wants_dark() else LIGHT
        self.tracks: list = []
        self.checked: set = set()
        self.results: dict = {}  # row index -> pipeline.Result
        self.run_indices: list = []  # rows in the current/last download run
        self.playlist_name = ""
        self.last_output_dir = ""
        self.busy = False
        self.cancel_event = None
        self.started_at = 0.0
        self.cover_photo = None  # keep a ref so Tk doesn't GC it
        self._last_autofill = ""
        self.msg_queue: queue.Queue = queue.Queue()

        self._set_window_icon()
        self._build_menu()
        self._build_widgets()
        self._apply_theme()
        self._show_environment_warnings()
        threading.Thread(target=self._check_ytdlp_worker, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<FocusIn>", self._maybe_autofill, add="+")
        self.after(100, self._poll_queue)

    def _wants_dark(self) -> bool:
        theme = self.settings.get("theme", "system")
        return theme == "dark" or (theme == "system" and _system_prefers_dark())

    def _set_window_icon(self):
        icon_path = os.path.join(_assets_dir(), "icon.iconset", "icon_128x128.png")
        if ImageTk and os.path.exists(icon_path):
            try:
                photo = ImageTk.PhotoImage(Image.open(icon_path))
                self.iconphoto(True, photo)
                self._icon_ref = photo
            except Exception:  # noqa: BLE001
                pass

    # ---------- menu ----------

    def _build_menu(self):
        mod, accel = ("Command", "Cmd") if IS_MAC else ("Control", "Ctrl")
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Import CSV…", accelerator=f"{accel}+O", command=self.on_import_csv)
        file_menu.add_command(label="Open Output Folder", command=self.on_open_output)
        if not IS_MAC:  # macOS has Quit in the app menu already
            file_menu.add_separator()
            file_menu.add_command(label="Quit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        tools = tk.Menu(menubar, tearoff=False)
        tools.add_command(label="Add Lyrics to Existing Folder…", command=self.on_add_lyrics_folder)
        tools.add_command(label="Update yt-dlp", command=self.on_update_ytdlp,
                          state="normal" if env.can_self_update() else "disabled")
        tools.add_separator()
        tools.add_command(label="Show Log", command=self.on_open_log)
        menubar.add_cascade(label="Tools", menu=tools)

        view = tk.Menu(menubar, tearoff=False)
        self.theme_var = tk.StringVar(value=self.settings.get("theme", "system"))
        for value, label in (("system", "Match System"), ("light", "Light"), ("dark", "Dark")):
            view.add_radiobutton(label=label, value=value, variable=self.theme_var, command=self.on_theme_change)
        menubar.add_cascade(label="Appearance", menu=view)

        self.config(menu=menubar)
        self.bind_all(f"<{mod}-o>", lambda e: self.on_import_csv())

    # ---------- styling ----------

    def _apply_theme(self):
        c = self.c
        self.configure(bg=c["bg"])
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=c["bg"], foreground=c["text"], font=FONT)
        style.configure("TFrame", background=c["bg"])
        style.configure("Surface.TFrame", background=c["surface"])
        style.configure("Warn.TFrame", background=c["warn_bg"])
        style.configure("TLabel", background=c["bg"], foreground=c["text"], font=FONT)
        style.configure("Title.TLabel", font=("SF Pro Display", 18, "bold"))
        style.configure("Sub.TLabel", background=c["bg"], foreground=c["subtext"], font=FONT_SMALL)
        style.configure("Mono.TLabel", background=c["bg"], foreground=c["subtext"], font=FONT_MONO)
        style.configure("Detail.TLabel", background=c["bg"], foreground=c["failed"], font=FONT_SMALL)
        style.configure("Warn.TLabel", background=c["warn_bg"], foreground=c["warn_text"], font=FONT_SMALL)

        style.configure(
            "TEntry", fieldbackground=c["surface"], foreground=c["text"], insertcolor=c["text"],
            bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"], padding=8,
        )
        style.map("TEntry", fieldbackground=[("disabled", c["bg"])], foreground=[("disabled", c["dim"])])
        style.configure("Accent.TButton", background=c["accent"], foreground=c["accent_text"], borderwidth=0,
                        padding=(14, 8), font=FONT)
        style.map(
            "Accent.TButton",
            background=[("disabled", c["accent_disabled"]), ("active", c["accent_hover"])],
            foreground=[("disabled", c["dim"])],
        )
        style.configure("Ghost.TButton", background=c["bg"], foreground=c["text"], borderwidth=1, relief="solid",
                        bordercolor=c["border"], padding=(12, 7), font=FONT_SMALL)
        style.map("Ghost.TButton", background=[("active", c["hover"])], foreground=[("disabled", c["dim"])])
        style.configure("Warn.TButton", background=c["warn_bg"], foreground=c["warn_text"], borderwidth=1,
                        relief="solid", padding=(10, 4), font=FONT_SMALL)

        style.configure("Treeview", background=c["surface"], fieldbackground=c["surface"], foreground=c["text"],
                        rowheight=26, borderwidth=0, font=FONT_SMALL)
        style.configure("Treeview.Heading", background=c["bg"], foreground=c["subtext"], borderwidth=0,
                        font=("SF Pro Text", 10, "bold"))
        style.map("Treeview.Heading", background=[("active", c["hover"])])
        style.map("Treeview", background=[("selected", c["selected"])], foreground=[("selected", c["text"])])

        style.configure("TCheckbutton", background=c["bg"], foreground=c["text"], font=FONT_SMALL,
                        indicatorbackground=c["surface"], indicatorforeground=c["text"])
        style.map("TCheckbutton", background=[("active", c["bg"])])
        for widget in ("TCombobox", "TSpinbox"):
            style.configure(widget, fieldbackground=c["surface"], background=c["surface"], foreground=c["text"],
                            arrowcolor=c["text"], bordercolor=c["border"])
            style.map(widget, fieldbackground=[("readonly", c["surface"])], foreground=[("readonly", c["text"])])
        self.option_add("*TCombobox*Listbox.background", c["surface"])
        self.option_add("*TCombobox*Listbox.foreground", c["text"])
        style.configure("TScrollbar", background=c["bg"], troughcolor=c["surface"], bordercolor=c["border"],
                        arrowcolor=c["subtext"])
        style.configure("Minimal.Horizontal.TProgressbar", troughcolor=c["border"], background=c["accent"],
                        bordercolor=c["border"], lightcolor=c["accent"], darkcolor=c["accent"])

        self.cover_label.configure(bg=c["surface"], highlightbackground=c["border"])
        self.tree.tag_configure("odd", background=c["stripe"])
        self.tree.tag_configure("done", foreground=c["done"])
        self.tree.tag_configure("failed", foreground=c["failed"])
        self.tree.tag_configure("active", foreground=c["active"])
        self.tree.tag_configure("unchecked", foreground=c["dim"])
        if not self.tracks:
            self._set_cover_placeholder()

    def on_theme_change(self):
        self.settings["theme"] = self.theme_var.get()
        self.c = DARK if self._wants_dark() else LIGHT
        self._apply_theme()
        _save_settings(self.settings)

    # ---------- UI construction ----------

    def _build_widgets(self):
        s = self.settings
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill="both", expand=True)

        # Banner for problems that will make downloads fail.
        self.banner = ttk.Frame(outer, style="Warn.TFrame", padding=(10, 6))
        self.banner_label = ttk.Label(self.banner, style="Warn.TLabel", wraplength=640, justify="left")
        self.banner_label.pack(side="left", fill="x", expand=True)
        self.banner_btn = ttk.Button(self.banner, text="Update now", style="Warn.TButton", command=self.on_update_ytdlp)
        self.banner_lines: list = []

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 14))
        self.header = header
        self.cover_label = tk.Label(header, bd=0, highlightthickness=1)
        self.cover_label.pack(side="left")

        header_text = ttk.Frame(header)
        header_text.pack(side="left", padx=(12, 0), fill="y")
        self.title_label = ttk.Label(header_text, text="melograb", style="Title.TLabel")
        self.title_label.pack(anchor="w")
        self.subtitle_label = ttk.Label(
            header_text,
            text="Paste Spotify or YouTube Music links (playlist, album, song, artist) — or type a song",
            style="Sub.TLabel",
        )
        self.subtitle_label.pack(anchor="w")

        link_row = ttk.Frame(outer)
        link_row.pack(fill="x")
        self.link_var = tk.StringVar()
        self.link_entry = ttk.Entry(link_row, textvariable=self.link_var, font=FONT)
        self.link_entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.link_entry.bind("<Return>", lambda e: self.on_fetch())
        self.fetch_btn = ttk.Button(link_row, text="Fetch", style="Accent.TButton", command=self.on_fetch)
        self.fetch_btn.pack(side="left", padx=(8, 0))
        self.csv_btn = ttk.Button(link_row, text="Import CSV", style="Ghost.TButton", command=self.on_import_csv)
        self.csv_btn.pack(side="left", padx=(8, 0))

        status_row = ttk.Frame(outer)
        status_row.pack(fill="x", pady=(10, 8))
        self.status_var = tk.StringVar(value="Nothing loaded yet.")
        ttk.Label(status_row, textvariable=self.status_var, style="Sub.TLabel", wraplength=600, justify="left").pack(
            side="left", fill="x", expand=True
        )
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._apply_filter())
        ttk.Entry(status_row, textvariable=self.filter_var, width=18, font=FONT_SMALL).pack(side="right")
        ttk.Label(status_row, text="Filter", style="Sub.TLabel").pack(side="right", padx=(0, 6))

        # Track list
        list_frame = ttk.Frame(outer, style="Surface.TFrame")
        list_frame.pack(fill="both", expand=True)
        columns = ("sel", "#", "artist", "title", "album", "time", "status")
        # A small requested height lets the list (not the controls below it)
        # absorb shrinking; it still expands to fill the window.
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse", height=6)
        for col, label, w, anchor, stretch in [
            ("sel", CHECKED, 34, "center", False),
            ("#", "#", 40, "center", False),
            ("artist", "Artist", 160, "w", True),
            ("title", "Title", 220, "w", True),
            ("album", "Album", 160, "w", True),
            ("time", "Time", 52, "e", False),
            ("status", "Status", 110, "center", False),
        ]:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=w, minwidth=60 if stretch else w, anchor=anchor, stretch=stretch)
        self.tree.heading("sel", text=CHECKED, command=self.on_toggle_all)

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<space>", self._on_space)
        self.tree.bind("<Double-1>", self._on_double_click)
        for seq in (("<Button-2>", "<Control-Button-1>") if IS_MAC else ("<Button-3>",)):
            self.tree.bind(seq, self._on_right_click)
        self._build_row_menu()

        # Why did the selected track fail / what did it match?
        self.detail_var = tk.StringVar(value="")
        self.detail_label = ttk.Label(outer, textvariable=self.detail_var, style="Detail.TLabel", wraplength=820,
                                      justify="left")
        self.detail_label.pack(anchor="w", pady=(6, 0))

        # Options
        opts = ttk.Frame(outer)
        opts.pack(fill="x", pady=(10, 0))
        self.lyrics_var = tk.BooleanVar(value=s.get("fetch_lyrics", True))
        ttk.Checkbutton(opts, text="Fetch synced lyrics", variable=self.lyrics_var).pack(side="left")
        self.sponsorblock_var = tk.BooleanVar(value=s.get("remove_non_music", True))
        ttk.Checkbutton(opts, text="Cut non-music intros/outros", variable=self.sponsorblock_var).pack(
            side="left", padx=(16, 0)
        )
        self.m3u_var = tk.BooleanVar(value=s.get("write_playlist", True))
        ttk.Checkbutton(opts, text="Write .m3u8 playlist", variable=self.m3u_var).pack(side="left", padx=(16, 0))

        opts2 = ttk.Frame(outer)
        opts2.pack(fill="x", pady=(10, 0))
        ttk.Label(opts2, text="Format", style="Sub.TLabel").pack(side="left")
        fmt = s.get("format", "mp3")
        self.format_var = tk.StringVar(value=fmt if fmt in downloader.FORMATS else "mp3")
        ttk.Combobox(opts2, textvariable=self.format_var, values=list(downloader.FORMATS), width=5,
                     state="readonly").pack(side="left", padx=(6, 14))
        ttk.Label(opts2, text="Quality", style="Sub.TLabel").pack(side="left")
        quality = s.get("quality", "best")
        self.quality_var = tk.StringVar(value=quality if quality in downloader.QUALITIES else "best")
        ttk.Combobox(opts2, textvariable=self.quality_var, values=list(downloader.QUALITIES), width=5,
                     state="readonly").pack(side="left", padx=(6, 14))
        ttk.Label(opts2, text="Workers", style="Sub.TLabel").pack(side="left")
        self.workers_var = tk.StringVar(value=str(s.get("workers", 4)))
        ttk.Spinbox(opts2, from_=1, to=10, textvariable=self.workers_var, width=3).pack(side="left", padx=(6, 14))
        ttk.Label(opts2, text="Output", style="Sub.TLabel").pack(side="left")
        self.output_var = tk.StringVar(value=s.get("output_dir") or DEFAULT_OUTPUT)
        ttk.Entry(opts2, textvariable=self.output_var).pack(side="left", fill="x", expand=True, padx=(6, 4))
        ttk.Button(opts2, text="Browse", style="Ghost.TButton", command=self.on_browse_output).pack(side="left")

        opts3 = ttk.Frame(outer)
        opts3.pack(fill="x", pady=(10, 0))
        ttk.Label(opts3, text="Layout", style="Sub.TLabel").pack(side="left")
        if "template" in s:
            template = s["template"]
        else:  # settings from 0.2 had a group-by-album toggle instead
            template = templates.DEFAULT_TEMPLATE if s.get("group_by_album", True) else templates.FLAT_TEMPLATE
        self.template_var = tk.StringVar(value=template)
        self.layout_var = tk.StringVar(value=templates.preset_name(template) or CUSTOM_LAYOUT)
        layout = ttk.Combobox(opts3, textvariable=self.layout_var, values=[*templates.PRESETS, CUSTOM_LAYOUT],
                              width=24, state="readonly")
        layout.pack(side="left", padx=(6, 8))
        layout.bind("<<ComboboxSelected>>", self._on_layout_selected)
        ttk.Entry(opts3, textvariable=self.template_var, font=FONT_MONO).pack(side="left", fill="x", expand=True)

        self.layout_preview_var = tk.StringVar()
        ttk.Label(outer, textvariable=self.layout_preview_var, style="Mono.TLabel").pack(anchor="w", pady=(4, 0))

        # Bottom bar: progress, then actions on the right (Retry/Open Folder
        # appear left of Download after a run).
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(14, 0))
        self.download_btn = ttk.Button(
            actions, text="Download All", style="Accent.TButton", command=self.on_download, state="disabled"
        )
        self.download_btn.pack(side="right")
        self.progress = ttk.Progressbar(actions, mode="determinate", style="Minimal.Horizontal.TProgressbar")
        self.progress.pack(side="left", fill="x", expand=True)
        self.progress_pct = ttk.Label(actions, text="", style="Sub.TLabel", width=5, anchor="e")
        self.progress_pct.pack(side="left", padx=(6, 12))
        self.open_folder_btn = ttk.Button(
            actions, text="Open Folder", style="Ghost.TButton", command=self.on_open_folder
        )
        self.retry_btn = ttk.Button(actions, text="Retry Failed", style="Ghost.TButton", command=self.on_retry_failed)

        self.format_var.trace_add("write", lambda *_: self._update_layout_preview())
        self.template_var.trace_add("write", lambda *_: self._on_template_edited())
        self._update_layout_preview()

    def _build_row_menu(self):
        m = tk.Menu(self, tearoff=False)
        m.add_command(label="Open on YouTube", command=lambda: self._row_action("youtube"))
        m.add_command(label="Use a Different YouTube Link…", command=lambda: self._row_action("relink"))
        m.add_command(label="Download Just This", command=lambda: self._row_action("download"))
        m.add_command(label="Show in Folder", command=lambda: self._row_action("reveal"))
        m.add_separator()
        m.add_command(label="Copy Error", command=lambda: self._row_action("copy_error"))
        self.row_menu = m

    # ---------- banner ----------

    def _add_banner_line(self, text: str, with_update_button: bool = False):
        self.banner_lines.append(f"⚠  {text}")
        self.banner_label.configure(text="\n".join(self.banner_lines))
        if with_update_button and env.can_self_update():
            self.banner_btn.pack(side="right", padx=(10, 0))
        if not self.banner.winfo_ismapped():
            self.banner.pack(fill="x", pady=(0, 12), before=self.header)

    def _show_environment_warnings(self):
        for w in env.environment_warnings():
            self._add_banner_line(w)

    def _check_ytdlp_worker(self):
        warning = env.ytdlp_update_warning()
        if warning:
            self.msg_queue.put(("banner", warning, True))

    # ---------- cover art ----------

    def _set_cover_placeholder(self):
        if not Image:
            return
        img = Image.new("RGBA", (56, 56), (0, 0, 0, 0))
        ImageDraw.Draw(img).rounded_rectangle([0, 0, 55, 55], radius=10, fill=self.c["placeholder"])
        self.cover_photo = ImageTk.PhotoImage(img)
        self.cover_label.configure(image=self.cover_photo)

    def _set_cover_from_url(self, url: str):
        if not Image or not url:
            return
        threading.Thread(target=self._fetch_cover_worker, args=(url,), daemon=True).start()

    def _fetch_cover_worker(self, url: str):
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            img = Image.open(io.BytesIO(resp.content)).convert("RGBA").resize((56, 56), Image.LANCZOS)
            mask = Image.new("L", (56, 56), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, 55, 55], radius=10, fill=255)
            img.putalpha(mask)
            self.msg_queue.put(("cover", img))
        except Exception:  # noqa: BLE001 - cover art is cosmetic, never fatal
            pass

    # ---------- state helpers ----------

    def _set_busy(self, busy: bool):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.fetch_btn.config(state=state)
        self.csv_btn.config(state=state)
        self.link_entry.config(state=state)
        if busy:
            self.retry_btn.pack_forget()
            self.open_folder_btn.pack_forget()

    def _workers(self) -> int:
        try:
            return min(16, max(1, int(self.workers_var.get())))
        except (TypeError, ValueError):
            return 4

    def _pipeline_options(self) -> pipeline.Options:
        return pipeline.Options(
            fmt=self.format_var.get(),
            template=self.template_var.get().strip(),
            fetch_lyrics=self.lyrics_var.get(),
            quality=self.quality_var.get(),
            remove_non_music=self.sponsorblock_var.get(),
            workers=self._workers(),
        )

    def _persist_settings(self):
        self.settings.update(
            output_dir=self.output_var.get().strip() or DEFAULT_OUTPUT,
            format=self.format_var.get(),
            quality=self.quality_var.get(),
            workers=self._workers(),
            template=self.template_var.get().strip() or templates.DEFAULT_TEMPLATE,
            fetch_lyrics=self.lyrics_var.get(),
            remove_non_music=self.sponsorblock_var.get(),
            write_playlist=self.m3u_var.get(),
            theme=self.theme_var.get(),
        )
        self.settings.pop("group_by_album", None)
        _save_settings(self.settings)

    def _row_tags(self, idx: int, status: str = "") -> tuple:
        tags = ["odd"] if idx % 2 else []
        if idx not in self.checked:
            tags.append("unchecked")
        elif status in ("done", "skipped", "duplicate"):
            tags.append("done")
        elif status in ("failed", "cancelled"):
            tags.append("failed")
        elif status == "downloading":
            tags.append("active")
        return tuple(tags)

    def _set_row(self, idx: int, status: str, album: str = ""):
        iid = str(idx)
        if not self.tree.exists(iid):
            return
        self.tree.set(iid, "status", STATUS_LABELS.get(status, status))
        if album:
            self.tree.set(iid, "album", album)
        self.tree.item(iid, tags=self._row_tags(idx, status))

    def _refresh_checks(self):
        for idx in range(len(self.tracks)):
            result = self.results.get(idx)
            self.tree.set(str(idx), "sel", CHECKED if idx in self.checked else UNCHECKED)
            self.tree.item(str(idx), tags=self._row_tags(idx, result.status if result else ""))
        all_checked = bool(self.tracks) and len(self.checked) == len(self.tracks)
        self.tree.heading("sel", text=CHECKED if all_checked else UNCHECKED)
        if not self.busy:
            n = len(self.checked)
            if not self.tracks or n == len(self.tracks):
                text = "Download All" if len(self.tracks) != 1 else "Download"
            elif n == 0:
                text = "Download"
            else:
                text = f"Download {n} Selected"
            self.download_btn.config(text=text, state="normal" if n else "disabled")

    def _toggle(self, idx: int):
        if self.busy:
            return
        self.checked.symmetric_difference_update({idx})
        self._refresh_checks()

    def _selected_index(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    # ---------- layout / template ----------

    def _on_layout_selected(self, _event=None):
        preset = templates.PRESETS.get(self.layout_var.get())
        if preset:
            self.template_var.set(preset)

    def _on_template_edited(self):
        name = templates.preset_name(self.template_var.get().strip())
        self.layout_var.set(name or CUSTOM_LAYOUT)
        self._update_layout_preview()

    def _update_layout_preview(self):
        sample = self.tracks[0] if self.tracks else SAMPLE_TRACK
        template = self.template_var.get().strip()
        try:
            templates.validate(template)
            path = templates.render(template, sample).replace(os.sep, "/")
            self.layout_preview_var.set(f"→ {path}.{self.format_var.get()}")
        except ValueError as e:
            self.layout_preview_var.set(f"✗ {e}")

    # ---------- filter ----------

    def _apply_filter(self):
        needle = self.filter_var.get().strip().lower()
        pos = 0
        for idx, t in enumerate(self.tracks):
            iid = str(idx)
            if not self.tree.exists(iid):  # list is being (re)built
                continue
            if not needle or needle in f"{t.artist} {t.title} {t.album}".lower():
                self.tree.move(iid, "", pos)
                pos += 1
            else:
                self.tree.detach(iid)

    # ---------- clipboard autofill ----------

    def _maybe_autofill(self, event):
        """When the window is activated with a Spotify/YouTube link on the
        clipboard and an empty input box, drop it in (once per link)."""
        if event.widget is not self or self.busy or self.link_var.get().strip():
            return
        try:
            clip = self.clipboard_get().strip()
        except tk.TclError:
            return
        first = clip.split()[0] if clip else ""
        if first and clip != self._last_autofill and (
            public_source.is_spotify_link(first) or ytmusic_source.is_youtube_link(first)
        ):
            self._last_autofill = clip
            self.link_var.set(clip)
            self.link_entry.select_range(0, "end")
            self.link_entry.focus_set()

    # ---------- actions ----------

    def on_fetch(self):
        text = self.link_var.get().strip()
        if not text or self.busy:
            return
        items = sources.split_inputs(text)
        try:
            for item in items:
                sources.describe(item)
        except ValueError as e:
            messagebox.showerror("Can't use that", str(e))
            return
        self._last_autofill = text
        self._set_busy(True)
        self.status_var.set("Fetching tracklist…")
        self.detail_var.set("")
        self.download_btn.config(state="disabled")
        self.tracks = []
        self.results = {}
        self.checked = set()
        self.tree.delete(*self.tree.get_children())
        threading.Thread(target=self._fetch_worker, args=(items,), daemon=True).start()

    def _fetch_worker(self, items):
        try:
            collection = sources.resolve_many(
                items,
                progress=lambda done, total: self.msg_queue.put(("status", f"Looking up albums… {done} / {total}")),
                on_source=lambda i, n, item: self.msg_queue.put(("status", f"Fetching {i + 1} / {n}…")),
            )
            self.msg_queue.put(("fetched", collection))
        except Exception as e:  # noqa: BLE001
            log.exception("fetch failed for %s", items)
            self.msg_queue.put(("error", str(e)))

    def on_import_csv(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*")])
        if not path:
            return
        try:
            if not path.lower().endswith(".csv"):
                raise ValueError("Please choose a .csv file.")
            collection = sources.resolve(path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("CSV import failed", str(e))
            return
        self._load_tracks(collection)

    def on_browse_output(self):
        path = filedialog.askdirectory(initialdir=self.output_var.get() or DEFAULT_OUTPUT)
        if path:
            self.output_var.set(path)

    def on_toggle_all(self):
        if self.busy or not self.tracks:
            return
        self.checked = set() if len(self.checked) == len(self.tracks) else set(range(len(self.tracks)))
        self._refresh_checks()

    def on_download(self, only=None):
        if self.busy:
            if self.cancel_event:
                self.cancel_event.set()
                self.download_btn.config(state="disabled", text="Cancelling…")
            return
        if not self.tracks:
            return
        if not env.has_ffmpeg():
            messagebox.showerror(
                "ffmpeg not found",
                "ffmpeg is required to convert audio.\nInstall it (macOS: `brew install ffmpeg`) and try again.",
            )
            return
        options = self._pipeline_options()
        try:
            options.validate()
        except ValueError as e:
            messagebox.showerror("Check the Layout", f"The file layout template isn't usable:\n{e}")
            return

        out_dir = self.output_var.get().strip() or DEFAULT_OUTPUT
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Can't use that output folder", str(e))
            return
        self._persist_settings()

        indices = sorted(self.checked if only is None else only)
        if not indices:
            return
        self._set_busy(True)
        self.cancel_event = threading.Event()
        self.started_at = time.monotonic()
        self.run_indices = indices
        self.download_btn.config(text="Cancel", state="normal")
        self.detail_var.set("")
        self.progress.config(value=0, maximum=len(indices))
        self.progress_pct.config(text="")
        self.status_var.set(f"Downloading 0 / {len(indices)}…")
        for i in indices:
            self.results.pop(i, None)
            self._set_row(i, "queued")

        threading.Thread(
            target=self._download_worker,
            args=(indices, out_dir, options, self.m3u_var.get(), self.cancel_event),
            daemon=True,
        ).start()

    def _download_worker(self, indices, out_dir, options, write_m3u, cancel):
        subset = [self.tracks[i] for i in indices]
        try:
            pipeline.run(
                subset,
                out_dir,
                options,
                on_start=lambda j: self.msg_queue.put(("started", indices[j])),
                on_result=lambda j, r: self.msg_queue.put(("result", indices[j], r)),
                cancel=cancel,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("download run crashed")
            self.msg_queue.put(("error", str(e)))
        self.msg_queue.put(("done", out_dir, write_m3u))

    def on_retry_failed(self):
        failed = [i for i, r in self.results.items() if not r.ok]
        if failed:
            self.on_download(only=sorted(failed))

    def on_open_folder(self):
        if self.last_output_dir and os.path.isdir(self.last_output_dir):
            _open_path(self.last_output_dir)

    def on_open_output(self):
        path = self.output_var.get().strip() or DEFAULT_OUTPUT
        if os.path.isdir(path):
            _open_path(path)
        else:
            messagebox.showinfo("Nothing there yet", f"{path} doesn't exist yet — it's created on first download.")

    def on_open_log(self):
        path = env.log_path()
        if os.path.exists(path):
            _open_path(path)
        else:
            messagebox.showinfo("No log yet", f"Nothing has been logged yet.\n({path})")

    def on_update_ytdlp(self):
        if not env.can_self_update():
            messagebox.showinfo("Update yt-dlp", "This build can't update itself — download a newer melograb.app.")
            return
        self.banner_btn.config(state="disabled", text="Updating…")
        self.status_var.set("Updating yt-dlp…")

        def work():
            ok, output = env.update_ytdlp()
            self.msg_queue.put(("updated", ok, output))

        threading.Thread(target=work, daemon=True).start()

    def on_add_lyrics_folder(self):
        if self.busy:
            return
        folder = filedialog.askdirectory(
            title="Add lyrics to every song in…", initialdir=self.output_var.get() or DEFAULT_OUTPUT
        )
        if not folder:
            return
        files = list(library.iter_audio_files(folder))
        if not files:
            messagebox.showinfo("No music found", f"No audio files in {folder}.")
            return
        self._set_busy(True)
        self.progress.config(value=0, maximum=len(files))
        self.status_var.set(f"Adding lyrics to {len(files)} files…")
        lock = threading.Lock()
        counter = [0]

        def on_result(_path, _outcome):
            with lock:
                counter[0] += 1
                n = counter[0]
            self.msg_queue.put(("lyrics_progress", n, len(files)))

        def work():
            results = library.add_lyrics_to_folder(folder, workers=self._workers(), on_result=on_result)
            self.msg_queue.put(("lyrics_done", folder, results))

        threading.Thread(target=work, daemon=True).start()

    # ---------- tree interactions ----------

    def _on_tree_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell" or self.tree.identify_column(event.x) != "#1":
            return None
        row = self.tree.identify_row(event.y)
        if row:
            self._toggle(int(row))
            return "break"
        return None

    def _on_space(self, _event=None):
        idx = self._selected_index()
        if idx is not None:
            self._toggle(idx)
        return "break"

    def _on_select(self, _event=None):
        idx = self._selected_index()
        result = self.results.get(idx) if idx is not None else None
        if result and result.error and not result.ok:
            self.detail_label.configure(style="Detail.TLabel")
            self.detail_var.set(f"#{idx + 1} {result.track.display_name} — {result.error}")
        elif result and result.match_title:
            self.detail_label.configure(style="Sub.TLabel")
            self.detail_var.set(f"#{idx + 1} matched “{result.match_title}” — right-click to change it")
        else:
            self.detail_var.set("")

    def _on_double_click(self, event):
        row = self.tree.identify_row(event.y)
        result = self.results.get(int(row)) if row else None
        if result and result.path and os.path.exists(result.path):
            _reveal(result.path)

    def _on_right_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        idx = int(row)
        result = self.results.get(idx)
        video_id = (result.video_id if result else "") or self.tracks[idx].video_id
        idle = "disabled" if self.busy else "normal"
        states = [
            "normal" if video_id else "disabled",  # Open on YouTube
            idle,  # Use a different link
            idle,  # Download just this
            "normal" if result and result.path and os.path.exists(result.path) else "disabled",  # Show in folder
            None,  # separator
            "normal" if result and result.error and not result.ok else "disabled",  # Copy error
        ]
        for i, state in enumerate(states):
            if state:
                self.row_menu.entryconfig(i, state=state)
        try:
            self.row_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.row_menu.grab_release()

    def _row_action(self, action: str):
        idx = self._selected_index()
        if idx is None:
            return
        track = self.tracks[idx]
        result = self.results.get(idx)
        if action == "youtube":
            video_id = (result.video_id if result else "") or track.video_id
            if video_id:
                webbrowser.open(f"https://music.youtube.com/watch?v={video_id}")
        elif action == "reveal" and result and result.path:
            _reveal(result.path)
        elif action == "copy_error" and result and result.error:
            self.clipboard_clear()
            self.clipboard_append(f"{track.display_name}: {result.error}")
        elif action == "download":
            self.on_download(only=[idx])
        elif action == "relink":
            self._relink(idx)

    def _relink(self, idx: int):
        """Let the user fix a wrong/failed match by pasting the right video."""
        track = self.tracks[idx]
        url = simpledialog.askstring(
            "Use a different YouTube link",
            f"Paste the YouTube / YouTube Music link to use for\n“{track.display_name}”:",
            parent=self,
        )
        if not url:
            return
        try:
            kind, video_id = ytmusic_source.parse_youtube_url(url)
        except ValueError as e:
            messagebox.showerror("Not a YouTube link", str(e))
            return
        if kind != "video":
            messagebox.showerror("Not a single video", "That's a playlist/album link — paste a link to one video.")
            return
        track.video_id = video_id
        # Unknown upload type: treat as a video so non-music parts get cut.
        track.video_type = "video"
        track.replace_existing = True
        self.checked.add(idx)
        self.on_download(only=[idx])

    def on_close(self):
        if self.busy and not messagebox.askyesno("Quit melograb?", "Work is still running. Quit anyway?"):
            return
        if self.cancel_event:
            self.cancel_event.set()
        self._persist_settings()
        self.destroy()

    # ---------- queue polling (keeps all Tk updates on the main thread) ----------

    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                getattr(self, f"_on_msg_{msg[0]}")(*msg[1:])
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _on_msg_status(self, text):
        self.status_var.set(text)

    def _on_msg_banner(self, text, with_update_button):
        self._add_banner_line(text, with_update_button)

    def _on_msg_fetched(self, collection):
        self._set_busy(False)
        self._load_tracks(collection)

    def _on_msg_error(self, text):
        self._set_busy(False)
        self.status_var.set("Error.")
        self._refresh_checks()
        messagebox.showerror("Something went wrong", text)

    def _on_msg_cover(self, img):
        self.cover_photo = ImageTk.PhotoImage(img)
        self.cover_label.configure(image=self.cover_photo)

    def _on_msg_started(self, idx):
        self._set_row(idx, "downloading")

    def _on_msg_result(self, idx, result):
        self.results[idx] = result
        self._set_row(idx, result.status, result.track.album)
        total = len(self.run_indices)
        finished = sum(1 for i in self.run_indices if i in self.results)
        self.progress.config(value=finished)
        self.progress_pct.config(text=f"{int(finished / total * 100)}%")
        self.status_var.set(f"Downloading {finished} / {total}…")
        if self._selected_index() == idx:
            self._on_select()

    def _on_msg_done(self, out_dir, write_m3u):
        self._set_busy(False)
        self.last_output_dir = out_dir
        for idx in self.run_indices:
            self.tracks[idx].replace_existing = False

        ordered = [self.results[i] for i in sorted(self.results)]
        if write_m3u and len(ordered) > 1:
            try:
                pipeline.write_m3u(ordered, out_dir, self.playlist_name or "playlist")
            except OSError:
                log.exception("couldn't write playlist file")

        this_run = [self.results[i] for i in self.run_indices if i in self.results]
        ok = sum(r.ok for r in this_run)
        failed = [r for r in this_run if not r.ok]
        summary = f"Finished — {ok} saved to {out_dir}"
        if failed:
            reasons = pipeline.summarize_failures(this_run)
            summary += f", {len(failed)} failed"
            if reasons:
                reason, count = reasons[0]
                summary += f" (most common: {reason})" if count > 1 else f" ({reason})"
            self.detail_label.configure(style="Detail.TLabel")
            self.detail_var.set("Click a red row to see why · right-click it to pick a different YouTube link.")
            self.retry_btn.pack(side="right", padx=(0, 8), after=self.download_btn)
        self.status_var.set(summary)
        if ok:
            self.open_folder_btn.pack(side="right", padx=(0, 8), after=self.download_btn)
        self._refresh_checks()
        if time.monotonic() - self.started_at > 15 and self.focus_displayof() is None:
            _notify("melograb", f"{ok} downloaded" + (f", {len(failed)} failed" if failed else ""))

    def _on_msg_updated(self, ok, output):
        self.banner_btn.config(state="normal", text="Update now")
        if ok:
            self.banner_btn.pack_forget()
            self.status_var.set("yt-dlp updated — restart melograb to use it.")
            messagebox.showinfo("yt-dlp updated", "Done. Quit and reopen melograb to use the new version.")
        else:
            self.status_var.set("yt-dlp update failed.")
            messagebox.showerror("Update failed", output or "pip failed without output.")

    def _on_msg_lyrics_progress(self, n, total):
        self.progress.config(value=n)
        self.progress_pct.config(text=f"{int(n / total * 100)}%")
        self.status_var.set(f"Adding lyrics… {n} / {total}")

    def _on_msg_lyrics_done(self, folder, results):
        self._set_busy(False)
        labels = {"synced": "synced", "plain": "plain", "has lyrics": "already had lyrics",
                  "not found": "not found", "no tags": "untagged", "error": "errors"}
        counts: dict = {}
        for outcome in results.values():
            key = "error" if outcome.startswith("error") else outcome
            counts[key] = counts.get(key, 0) + 1
        summary = ", ".join(f"{n} {labels.get(k, k)}" for k, n in sorted(counts.items(), key=lambda p: -p[1]))
        self.status_var.set(f"Lyrics in {folder}: {summary}")
        self._refresh_checks()

    def _load_tracks(self, collection):
        tracks = collection.tracks
        self.playlist_name = collection.name
        self.tracks = tracks
        self.results = {}
        self.run_indices = []
        self.checked = set(range(len(tracks)))
        self.filter_var.set("")
        self.tree.delete(*self.tree.get_children())
        for i, t in enumerate(tracks):
            self.tree.insert(
                "", "end", iid=str(i),
                values=(CHECKED, i + 1, t.artist, t.title, t.album, t.duration_text, ""),
                tags=self._row_tags(i),
            )
        self.title_label.config(text=collection.name or "melograb")
        minutes = sum(t.duration_ms for t in tracks) // 60_000
        if minutes >= 60:
            length = f" · {minutes // 60} h {minutes % 60} min"
        else:
            length = f" · {minutes} min" if minutes else ""
        self.subtitle_label.config(text=f"{len(tracks)} track{'s' if len(tracks) != 1 else ''}{length}")
        self.status_var.set(
            f"{len(tracks)} tracks found. Untick any you don't want, then Download."
            if len(tracks) > 1 else "Ready to download."
        )
        self.detail_var.set("")
        self.open_folder_btn.pack_forget()
        self.retry_btn.pack_forget()
        self.progress.config(value=0)
        self.progress_pct.config(text="")
        self._refresh_checks()
        self._update_layout_preview()
        self._set_cover_placeholder()
        cover = collection.cover_url or (tracks[0].cover_url if tracks else "")
        if cover:
            self._set_cover_from_url(cover)


def main():
    env.ensure_tool_paths()
    env.setup_logging()
    logging.getLogger(__name__).info("melograb %s starting", __version__)
    app = MelograbApp()
    app.mainloop()


if __name__ == "__main__":
    main()
