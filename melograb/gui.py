"""Minimalist desktop GUI for melograb: paste a public Spotify playlist/album
link, preview the tracklist, then download + tag everything locally --
grouped into per-album folders, with synced lyrics embedded.

No login, no API key, no Premium -- reads the same public page data
Spotify serves to any logged-out visitor (see public_source.py).
"""
from __future__ import annotations

import io
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import requests

from . import __version__, csv_source, downloader, env, pipeline, public_source

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:  # Pillow is optional -- cover art just won't render
    Image = ImageDraw = ImageTk = None

log = logging.getLogger(__name__)

DEFAULT_OUTPUT = os.path.join(os.path.expanduser("~"), "Music", "melograb")
SETTINGS_PATH = os.path.join(env.config_dir(), "settings.json")


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
    if sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    elif sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", path], check=False)


# ---- minimal, neutral palette ----
BG = "#fafafa"
SURFACE = "#ffffff"
STRIPE = "#f7f7f8"
BORDER = "#e4e4e7"
TEXT = "#18181b"
SUBTEXT = "#71717a"
ACCENT = "#18181b"
ACCENT_TEXT = "#ffffff"
DONE = "#16a34a"
FAILED = "#dc2626"
ACTIVE = "#2563eb"
WARN_BG = "#fef3c7"
WARN_TEXT = "#92400e"
FONT = ("SF Pro Text", 12)
FONT_SMALL = ("SF Pro Text", 11)

STATUS_LABELS = {
    "pending": "",
    "queued": "queued",
    "downloading": "downloading…",
    "done": "done",
    "skipped": "already here",
    "duplicate": "duplicate",
    "failed": "failed",
    "cancelled": "cancelled",
}


class MelograbApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("melograb")
        self.geometry("840x720")
        self.minsize(720, 640)
        self.configure(bg=BG)

        self.settings = _load_settings()
        self.tracks = []
        self.results: dict = {}  # row index -> pipeline.Result
        self.playlist_name = ""
        self.last_output_dir = ""
        self.busy = False
        self.cancel_event = None
        self.cover_photo = None  # keep a ref so Tk doesn't GC it
        self.msg_queue: queue.Queue = queue.Queue()

        self._set_window_icon()
        self._build_style()
        self._build_widgets()
        self._show_environment_warnings()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._poll_queue)

    def _set_window_icon(self):
        icon_path = os.path.join(_assets_dir(), "icon.iconset", "icon_128x128.png")
        if ImageTk and os.path.exists(icon_path):
            try:
                photo = ImageTk.PhotoImage(Image.open(icon_path))
                self.iconphoto(True, photo)
                self._icon_ref = photo
            except Exception:  # noqa: BLE001
                pass

    # ---------- styling ----------

    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=BG, foreground=TEXT, font=FONT)
        style.configure("TFrame", background=BG)
        style.configure("Surface.TFrame", background=SURFACE)
        style.configure("TLabel", background=BG, foreground=TEXT, font=FONT)
        style.configure("Sub.TLabel", background=BG, foreground=SUBTEXT, font=FONT_SMALL)
        style.configure("Detail.TLabel", background=BG, foreground=FAILED, font=FONT_SMALL)
        style.configure("Warn.TLabel", background=WARN_BG, foreground=WARN_TEXT, font=FONT_SMALL, padding=(10, 6))

        style.configure(
            "TEntry",
            fieldbackground=SURFACE,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=8,
        )
        style.configure(
            "Accent.TButton",
            background=ACCENT,
            foreground=ACCENT_TEXT,
            borderwidth=0,
            padding=(14, 8),
            font=FONT,
        )
        style.map(
            "Accent.TButton",
            background=[("disabled", "#d4d4d8"), ("active", "#3f3f46")],
            foreground=[("disabled", "#fafafa")],
        )

        style.configure(
            "Ghost.TButton",
            background=BG,
            foreground=TEXT,
            borderwidth=1,
            relief="solid",
            padding=(12, 7),
            font=FONT_SMALL,
        )
        style.map("Ghost.TButton", background=[("active", "#f0f0f0")], foreground=[("disabled", "#a1a1aa")])

        style.configure(
            "Treeview",
            background=SURFACE,
            fieldbackground=SURFACE,
            foreground=TEXT,
            rowheight=26,
            borderwidth=0,
            font=FONT_SMALL,
        )
        style.configure(
            "Treeview.Heading",
            background=BG,
            foreground=SUBTEXT,
            borderwidth=0,
            font=("SF Pro Text", 10, "bold"),
        )
        style.map("Treeview", background=[("selected", "#e4e4e7")], foreground=[("selected", TEXT)])

        style.configure("TCheckbutton", background=BG, foreground=TEXT, font=FONT_SMALL)
        style.configure("TCombobox", fieldbackground=SURFACE, background=SURFACE)
        style.configure("TSpinbox", fieldbackground=SURFACE, background=SURFACE)
        style.configure(
            "Minimal.Horizontal.TProgressbar",
            troughcolor=BORDER,
            background=ACCENT,
            bordercolor=BORDER,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
        )

    # ---------- UI construction ----------

    def _build_widgets(self):
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill="both", expand=True)

        self.warn_label = ttk.Label(outer, style="Warn.TLabel", wraplength=740, justify="left")

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 14))
        self.header = header

        self.cover_label = tk.Label(header, bg=SURFACE, bd=0, highlightthickness=1, highlightbackground=BORDER)
        self.cover_label.pack(side="left")
        self._set_cover_placeholder()

        header_text = ttk.Frame(header)
        header_text.pack(side="left", padx=(12, 0), fill="y")
        self.title_label = ttk.Label(header_text, text="melograb", font=("SF Pro Display", 18, "bold"))
        self.title_label.pack(anchor="w")
        self.subtitle_label = ttk.Label(
            header_text, text="Paste a public Spotify playlist or album link", style="Sub.TLabel"
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

        self.status_var = tk.StringVar(value="Nothing loaded yet.")
        ttk.Label(outer, textvariable=self.status_var, style="Sub.TLabel", wraplength=760, justify="left").pack(
            anchor="w", pady=(10, 8)
        )

        # Track list
        list_frame = ttk.Frame(outer, style="Surface.TFrame")
        list_frame.pack(fill="both", expand=True)

        columns = ("#", "artist", "title", "album", "status")
        # A small requested height lets the list (not the controls below it)
        # absorb shrinking; it still expands to fill the window.
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse", height=6)
        for col, label, w, anchor, stretch in [
            ("#", "#", 40, "center", False),
            ("artist", "Artist", 170, "w", True),
            ("title", "Title", 230, "w", True),
            ("album", "Album", 170, "w", True),
            ("status", "Status", 110, "center", False),
        ]:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=w, anchor=anchor, stretch=stretch)

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("odd", background=STRIPE)
        self.tree.tag_configure("done", foreground=DONE)
        self.tree.tag_configure("failed", foreground=FAILED)
        self.tree.tag_configure("active", foreground=ACTIVE)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double_click)

        # Why did the selected track fail?
        self.detail_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.detail_var, style="Detail.TLabel", wraplength=760, justify="left").pack(
            anchor="w", pady=(6, 0)
        )

        # Options row
        opts = ttk.Frame(outer)
        opts.pack(fill="x", pady=(10, 0))

        s = self.settings
        self.album_var = tk.BooleanVar(value=s.get("group_by_album", True))
        ttk.Checkbutton(opts, text="Group by album", variable=self.album_var).pack(side="left")
        self.lyrics_var = tk.BooleanVar(value=s.get("fetch_lyrics", True))
        ttk.Checkbutton(opts, text="Fetch synced lyrics", variable=self.lyrics_var).pack(side="left", padx=(16, 0))
        self.m3u_var = tk.BooleanVar(value=s.get("write_playlist", True))
        ttk.Checkbutton(opts, text="Write .m3u8 playlist", variable=self.m3u_var).pack(side="left", padx=(16, 0))

        opts2 = ttk.Frame(outer)
        opts2.pack(fill="x", pady=(10, 0))

        ttk.Label(opts2, text="Format", style="Sub.TLabel").pack(side="left")
        fmt = s.get("format", "mp3")
        self.format_var = tk.StringVar(value=fmt if fmt in downloader.FORMATS else "mp3")
        ttk.Combobox(
            opts2, textvariable=self.format_var, values=list(downloader.FORMATS), width=5, state="readonly"
        ).pack(side="left", padx=(6, 18))

        ttk.Label(opts2, text="Workers", style="Sub.TLabel").pack(side="left")
        self.workers_var = tk.StringVar(value=str(s.get("workers", 4)))
        ttk.Spinbox(opts2, from_=1, to=10, textvariable=self.workers_var, width=3).pack(side="left", padx=(6, 18))

        ttk.Label(opts2, text="Output", style="Sub.TLabel").pack(side="left")
        self.output_var = tk.StringVar(value=s.get("output_dir") or DEFAULT_OUTPUT)
        ttk.Entry(opts2, textvariable=self.output_var).pack(side="left", fill="x", expand=True, padx=(6, 4))
        ttk.Button(opts2, text="Browse", style="Ghost.TButton", command=self.on_browse_output).pack(side="left")

        # Bottom bar: progress on its own row, actions right-aligned below.
        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(14, 0))
        self.progress = ttk.Progressbar(bottom, mode="determinate", style="Minimal.Horizontal.TProgressbar")
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.progress_pct = ttk.Label(bottom, text="", style="Sub.TLabel", width=5)
        self.progress_pct.pack(side="left")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(10, 0))
        self.log_btn = ttk.Button(actions, text="Show Log", style="Ghost.TButton", command=self.on_open_log)
        self.log_btn.pack(side="left")
        self.download_btn = ttk.Button(
            actions, text="Download All", style="Accent.TButton", command=self.on_download, state="disabled"
        )
        self.download_btn.pack(side="right")
        self.open_folder_btn = ttk.Button(
            actions, text="Open Folder", style="Ghost.TButton", command=self.on_open_folder
        )
        self.retry_btn = ttk.Button(actions, text="Retry Failed", style="Ghost.TButton", command=self.on_retry_failed)

    def _show_environment_warnings(self):
        warnings = env.environment_warnings()
        if warnings:
            self.warn_label.configure(text="\n".join(f"⚠  {w}" for w in warnings))
            self.warn_label.pack(fill="x", pady=(0, 12), before=self.header)

    # ---------- cover art ----------

    def _set_cover_placeholder(self):
        if not Image:
            return
        img = Image.new("RGBA", (56, 56), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([0, 0, 55, 55], radius=10, fill=(228, 228, 231, 255))
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

    def _persist_settings(self):
        self.settings.update(
            output_dir=self.output_var.get().strip() or DEFAULT_OUTPUT,
            format=self.format_var.get(),
            workers=self._workers(),
            group_by_album=self.album_var.get(),
            fetch_lyrics=self.lyrics_var.get(),
            write_playlist=self.m3u_var.get(),
        )
        _save_settings(self.settings)

    def _set_row(self, idx: int, status: str, album: str = ""):
        children = self.tree.get_children()
        if idx >= len(children):
            return
        item_id = children[idx]
        self.tree.set(item_id, "status", STATUS_LABELS.get(status, status))
        if album:
            self.tree.set(item_id, "album", album)
        tags = ["odd"] if idx % 2 else []
        if status in ("done", "skipped", "duplicate"):
            tags.append("done")
        elif status in ("failed", "cancelled"):
            tags.append("failed")
        elif status == "downloading":
            tags.append("active")
        self.tree.item(item_id, tags=tuple(tags))

    # ---------- actions ----------

    def on_fetch(self):
        url = self.link_var.get().strip()
        if not url or self.busy:
            return
        try:
            public_source.parse_spotify_url(url)
        except ValueError as e:
            messagebox.showerror("Not a Spotify link", str(e))
            return
        self._set_busy(True)
        self.status_var.set("Fetching tracklist…")
        self.detail_var.set("")
        self.download_btn.config(state="disabled")
        self.tracks = []
        self.results = {}
        self.tree.delete(*self.tree.get_children())
        threading.Thread(target=self._fetch_worker, args=(url,), daemon=True).start()

    def _fetch_worker(self, url: str):
        try:
            collection = public_source.fetch_collection(
                url, progress=lambda done, total: self.msg_queue.put(("enrich", done, total))
            )
            self.msg_queue.put(("fetched", collection))
        except Exception as e:  # noqa: BLE001
            log.exception("fetch failed for %s", url)
            self.msg_queue.put(("error", str(e)))

    def on_import_csv(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*")])
        if not path:
            return
        try:
            tracks = csv_source.fetch_tracks(path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("CSV import failed", str(e))
            return
        name = os.path.splitext(os.path.basename(path))[0]
        self._load_tracks(name, tracks, cover_url="")

    def on_browse_output(self):
        path = filedialog.askdirectory(initialdir=self.output_var.get() or DEFAULT_OUTPUT)
        if path:
            self.output_var.set(path)

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

        out_dir = self.output_var.get().strip() or DEFAULT_OUTPUT
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Can't use that output folder", str(e))
            return
        self._persist_settings()

        indices = list(only) if only is not None else list(range(len(self.tracks)))
        self._set_busy(True)
        self.cancel_event = threading.Event()
        self.download_btn.config(text="Cancel", state="normal")
        self.detail_var.set("")
        done_already = len(self.tracks) - len(indices)
        self.progress.config(value=done_already, maximum=len(self.tracks))
        self.status_var.set(f"Downloading {done_already} / {len(self.tracks)}…")
        for i in indices:
            self.results.pop(i, None)
            self._set_row(i, "queued")

        threading.Thread(
            target=self._download_worker,
            args=(
                indices,
                out_dir,
                self.format_var.get(),
                self._workers(),
                self.album_var.get(),
                self.lyrics_var.get(),
                self.m3u_var.get(),
                self.cancel_event,
            ),
            daemon=True,
        ).start()

    def _download_worker(self, indices, out_dir, fmt, workers, group_by_album, fetch_lyrics, write_m3u, cancel):
        subset = [self.tracks[i] for i in indices]
        try:
            pipeline.run(
                subset,
                out_dir,
                fmt,
                workers,
                group_by_album=group_by_album,
                fetch_lyrics=fetch_lyrics,
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

    def on_open_log(self):
        path = env.log_path()
        if os.path.exists(path):
            _open_path(path)
        else:
            messagebox.showinfo("No log yet", f"Nothing has been logged yet.\n({path})")

    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        result = self.results.get(idx)
        if result and result.error and not result.ok:
            self.detail_var.set(f"#{idx + 1} {result.track.display_name} — {result.error}")
        else:
            self.detail_var.set("")

    def _on_double_click(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        result = self.results.get(self.tree.index(sel[0]))
        if result and result.path and os.path.exists(result.path):
            _open_path(os.path.dirname(result.path))

    def on_close(self):
        if self.busy and not messagebox.askyesno("Quit melograb?", "Downloads are still running. Quit anyway?"):
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

    def _on_msg_enrich(self, done, total):
        self.status_var.set(f"Looking up albums… {done} / {total}")

    def _on_msg_fetched(self, collection):
        self._set_busy(False)
        self._load_tracks(collection.name, collection.tracks, collection.cover_url)

    def _on_msg_error(self, text):
        self._set_busy(False)
        self.download_btn.config(text="Download All", state="normal" if self.tracks else "disabled")
        self.status_var.set("Error.")
        messagebox.showerror("Something went wrong", text)

    def _on_msg_cover(self, img):
        self.cover_photo = ImageTk.PhotoImage(img)
        self.cover_label.configure(image=self.cover_photo)

    def _on_msg_started(self, idx):
        self._set_row(idx, "downloading")

    def _on_msg_result(self, idx, result):
        self.results[idx] = result
        self._set_row(idx, result.status, result.track.album)
        total = len(self.tracks)
        finished = len(self.results)
        self.progress.config(value=finished)
        self.progress_pct.config(text=f"{int(finished / total * 100)}%")
        self.status_var.set(f"Downloading {finished} / {total}…")
        sel = self.tree.selection()
        if sel and self.tree.index(sel[0]) == idx:
            self._on_select()

    def _on_msg_done(self, out_dir, write_m3u):
        self._set_busy(False)
        self.last_output_dir = out_dir
        self.download_btn.config(text="Download All", state="normal")

        ordered = [self.results[i] for i in sorted(self.results)]
        if write_m3u and ordered:
            try:
                pipeline.write_m3u(ordered, out_dir, self.playlist_name or "playlist")
            except OSError:
                log.exception("couldn't write playlist file")

        ok = sum(r.ok for r in ordered)
        failed = [r for r in ordered if not r.ok]
        summary = f"Finished — {ok} saved to {out_dir}"
        if failed:
            reasons = pipeline.summarize_failures(ordered)
            summary += f", {len(failed)} failed"
            if reasons:
                reason, count = reasons[0]
                summary += f" (most common: {reason})" if count > 1 else f" ({reason})"
            self.detail_var.set("Click a red row to see why it failed.")
            self.retry_btn.pack(side="right", padx=(0, 8), after=self.download_btn)
        self.status_var.set(summary)
        if ok:
            self.open_folder_btn.pack(side="right", padx=(0, 8), after=self.download_btn)

    def _load_tracks(self, name, tracks, cover_url):
        self.playlist_name = name
        self.tracks = tracks
        self.results = {}
        self.tree.delete(*self.tree.get_children())
        for i, t in enumerate(tracks):
            self.tree.insert(
                "", "end", values=(i + 1, t.artist, t.title, t.album, ""), tags=("odd",) if i % 2 else ()
            )
        self.title_label.config(text=name or "melograb")
        self.subtitle_label.config(text=f"{len(tracks)} tracks")
        self.status_var.set(f"{len(tracks)} tracks found. Review, then Download All.")
        self.detail_var.set("")
        self.download_btn.config(state="normal" if tracks else "disabled", text="Download All")
        self.open_folder_btn.pack_forget()
        self.retry_btn.pack_forget()
        self.progress.config(value=0)
        self.progress_pct.config(text="")
        self._set_cover_placeholder()
        cover = cover_url or (tracks[0].cover_url if tracks else "")
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
