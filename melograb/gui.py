"""Minimalist desktop GUI for melograb: paste a public Spotify playlist/album
link, preview the tracklist, then download + tag everything locally --
grouped into per-album folders, with synced lyrics embedded.

No login, no API key, no Premium -- reads the same public page data
Spotify serves to any logged-out visitor (see public_source.py).
"""
from __future__ import annotations

import io
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import requests

from . import csv_source, pipeline, public_source

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:  # Pillow is optional -- cover art just won't render
    Image = ImageDraw = ImageTk = None

DEFAULT_OUTPUT = os.path.join(os.path.expanduser("~"), "Music", "melograb")


def _assets_dir() -> str:
    # PyInstaller unpacks bundled data files under sys._MEIPASS at runtime.
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets")

# ---- minimal, neutral palette ----
BG = "#fafafa"
SURFACE = "#ffffff"
BORDER = "#e4e4e7"
TEXT = "#18181b"
SUBTEXT = "#71717a"
ACCENT = "#18181b"
ACCENT_TEXT = "#ffffff"
DONE = "#16a34a"
FAILED = "#dc2626"
FONT = ("SF Pro Text", 12)
FONT_SMALL = ("SF Pro Text", 11)
FONT_MONO = ("SF Mono", 11)


class MelograbApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("melograb")
        self.geometry("760x580")
        self.minsize(680, 480)
        self.configure(bg=BG)

        self.tracks = []
        self.playlist_name = ""
        self.last_output_dir = DEFAULT_OUTPUT
        self.cover_photo = None  # keep a ref so Tk doesn't GC it
        self.msg_queue: queue.Queue = queue.Queue()

        self._set_window_icon()
        self._build_style()
        self._build_widgets()
        self._check_ffmpeg()
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
        style.map("Accent.TButton", background=[("disabled", "#d4d4d8"), ("active", "#3f3f46")])

        style.configure(
            "Ghost.TButton",
            background=BG,
            foreground=TEXT,
            borderwidth=1,
            relief="solid",
            padding=(12, 7),
            font=FONT_SMALL,
        )
        style.map("Ghost.TButton", background=[("active", "#f0f0f0")])

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
        style.map("Treeview", background=[("selected", "#f0f0f0")], foreground=[("selected", TEXT)])

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

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 14))

        self.cover_label = tk.Label(header, bg=SURFACE, bd=0, highlightthickness=1, highlightbackground=BORDER)
        self.cover_label.pack(side="left")
        self._set_cover_placeholder()

        header_text = ttk.Frame(header)
        header_text.pack(side="left", padx=(12, 0), fill="y")
        ttk.Label(header_text, text="melograb", font=("SF Pro Display", 18, "bold")).pack(anchor="w")
        ttk.Label(
            header_text, text="Paste a public Spotify playlist or album link", style="Sub.TLabel"
        ).pack(anchor="w")

        link_row = ttk.Frame(outer)
        link_row.pack(fill="x")
        self.link_var = tk.StringVar()
        self.link_entry = ttk.Entry(link_row, textvariable=self.link_var, font=FONT)
        self.link_entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.link_entry.bind("<Return>", lambda e: self.on_fetch())
        ttk.Button(link_row, text="Fetch", style="Accent.TButton", command=self.on_fetch).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(link_row, text="Import CSV", style="Ghost.TButton", command=self.on_import_csv).pack(
            side="left", padx=(8, 0)
        )

        self.status_var = tk.StringVar(value="Nothing loaded yet.")
        ttk.Label(outer, textvariable=self.status_var, style="Sub.TLabel").pack(
            anchor="w", pady=(10, 8)
        )

        # Track list
        list_frame = ttk.Frame(outer, style="Surface.TFrame")
        list_frame.pack(fill="both", expand=True)

        columns = ("#", "artist", "title", "album", "status")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="none")
        for col, label, w, anchor in [
            ("#", "#", 36, "center"),
            ("artist", "Artist", 170, "w"),
            ("title", "Title", 220, "w"),
            ("album", "Album", 170, "w"),
            ("status", "Status", 90, "center"),
        ]:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=w, anchor=anchor)

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("done", foreground=DONE)
        self.tree.tag_configure("failed", foreground=FAILED)

        # Options row
        opts = ttk.Frame(outer)
        opts.pack(fill="x", pady=(14, 0))

        self.album_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Group by album", variable=self.album_var).pack(side="left")
        self.lyrics_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Fetch synced lyrics", variable=self.lyrics_var).pack(
            side="left", padx=(16, 0)
        )

        opts2 = ttk.Frame(outer)
        opts2.pack(fill="x", pady=(10, 0))

        ttk.Label(opts2, text="Format", style="Sub.TLabel").pack(side="left")
        self.format_var = tk.StringVar(value="mp3")
        ttk.Combobox(
            opts2, textvariable=self.format_var, values=["mp3", "m4a", "opus"], width=5, state="readonly"
        ).pack(side="left", padx=(6, 18))

        ttk.Label(opts2, text="Workers", style="Sub.TLabel").pack(side="left")
        self.workers_var = tk.IntVar(value=4)
        ttk.Spinbox(opts2, from_=1, to=10, textvariable=self.workers_var, width=3).pack(
            side="left", padx=(6, 18)
        )

        ttk.Label(opts2, text="Output", style="Sub.TLabel").pack(side="left")
        self.output_var = tk.StringVar(value=DEFAULT_OUTPUT)
        ttk.Entry(opts2, textvariable=self.output_var, width=26).pack(side="left", padx=(6, 4))
        ttk.Button(opts2, text="Browse", style="Ghost.TButton", command=self.on_browse_output).pack(
            side="left"
        )

        # Bottom bar
        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(14, 0))
        self.progress = ttk.Progressbar(bottom, mode="determinate", style="Minimal.Horizontal.TProgressbar")
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.progress_pct = ttk.Label(bottom, text="", style="Sub.TLabel", width=5)
        self.progress_pct.pack(side="left", padx=(0, 10))
        self.open_folder_btn = ttk.Button(
            bottom, text="Open Folder", style="Ghost.TButton", command=self.on_open_folder
        )
        self.download_btn = ttk.Button(
            bottom, text="Download All", style="Accent.TButton", command=self.on_download, state="disabled"
        )
        self.download_btn.pack(side="left")

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

    def on_open_folder(self):
        path = self.last_output_dir
        if not os.path.isdir(path):
            return
        if sys.platform == "darwin":
            subprocess.run(["open", path])
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", path])
        else:
            os.startfile(path)  # type: ignore[attr-defined]

    def _check_ffmpeg(self):
        if shutil.which("ffmpeg") is None:
            messagebox.showwarning(
                "ffmpeg not found",
                "ffmpeg is required to convert/tag audio.\nInstall it (e.g. `brew install ffmpeg`) and restart melograb.",
            )

    # ---------- actions ----------

    def on_fetch(self):
        url = self.link_var.get().strip()
        if not url:
            return
        self.status_var.set("Fetching playlist...")
        self.download_btn.config(state="disabled")
        self.tree.delete(*self.tree.get_children())
        threading.Thread(target=self._fetch_worker, args=(url,), daemon=True).start()

    def _fetch_worker(self, url: str):
        try:
            name, tracks = public_source.fetch_playlist(url)
            self.msg_queue.put(("fetched", name, tracks))
        except Exception as e:  # noqa: BLE001
            self.msg_queue.put(("error", str(e)))

    def on_import_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        try:
            tracks = csv_source.fetch_tracks(path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("CSV import failed", str(e))
            return
        self.playlist_name = os.path.basename(path)
        self._load_tracks(tracks)

    def on_browse_output(self):
        path = filedialog.askdirectory()
        if path:
            self.output_var.set(path)

    def on_download(self):
        if not self.tracks:
            return
        out_dir = self.output_var.get().strip() or DEFAULT_OUTPUT
        fmt = self.format_var.get()
        workers = max(1, self.workers_var.get())
        group_by_album = self.album_var.get()
        fetch_lyrics = self.lyrics_var.get()

        self.download_btn.config(state="disabled")
        self.progress.config(value=0, maximum=len(self.tracks))
        self.status_var.set(f"Downloading 0 / {len(self.tracks)}...")

        for i in self.tree.get_children():
            self.tree.set(i, "status", "queued")
            self.tree.item(i, tags=())

        threading.Thread(
            target=self._download_worker,
            args=(out_dir, fmt, workers, group_by_album, fetch_lyrics),
            daemon=True,
        ).start()

    def _download_worker(self, out_dir, fmt, workers, group_by_album, fetch_lyrics):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        os.makedirs(out_dir, exist_ok=True)
        done = 0
        total = len(self.tracks)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    pipeline.process_one, t, out_dir, fmt, group_by_album, fetch_lyrics
                ): idx
                for idx, t in enumerate(self.tracks)
            }
            for fut in as_completed(futures):
                idx = futures[fut]
                track, path, err = fut.result()
                done += 1
                self.msg_queue.put(("progress", idx, done, total, bool(path), err, track.album))
        self.msg_queue.put(("done",))

    # ---------- queue polling (keeps all Tk updates on the main thread) ----------

    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind = msg[0]
                if kind == "fetched":
                    _, name, tracks = msg
                    self.playlist_name = name
                    self._load_tracks(tracks)
                elif kind == "error":
                    self.status_var.set("Error.")
                    messagebox.showerror("Could not fetch playlist", msg[1])
                elif kind == "progress":
                    _, idx, done, total, ok, err, album = msg
                    item_id = self.tree.get_children()[idx]
                    self.tree.set(item_id, "status", "done" if ok else "failed")
                    if album:
                        self.tree.set(item_id, "album", album)
                    self.tree.item(item_id, tags=("done" if ok else "failed",))
                    self.progress.config(value=done)
                    self.progress_pct.config(text=f"{int(done / total * 100)}%")
                    self.status_var.set(f"Downloading {done} / {total}...")
                elif kind == "done":
                    self.last_output_dir = self.output_var.get().strip() or DEFAULT_OUTPUT
                    self.status_var.set(f"Finished -- saved to {self.last_output_dir}")
                    self.download_btn.config(state="normal")
                    self.open_folder_btn.pack(side="left", padx=(0, 8), before=self.download_btn)
                elif kind == "cover":
                    self.cover_photo = ImageTk.PhotoImage(msg[1])
                    self.cover_label.configure(image=self.cover_photo)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _load_tracks(self, tracks):
        self.tracks = tracks
        self.tree.delete(*self.tree.get_children())
        for i, t in enumerate(tracks, start=1):
            self.tree.insert("", "end", values=(i, t.artist, t.title, t.album, "pending"))
        label = f"“{self.playlist_name}” " if self.playlist_name else ""
        self.status_var.set(f"{label}{len(tracks)} tracks found. Review, then Download All.")
        self.download_btn.config(state="normal" if tracks else "disabled")
        self.open_folder_btn.pack_forget()
        self.progress.config(value=0)
        self.progress_pct.config(text="")
        self._set_cover_placeholder()
        if tracks and tracks[0].cover_url:
            self._set_cover_from_url(tracks[0].cover_url)


def main():
    app = MelograbApp()
    app.mainloop()


if __name__ == "__main__":
    main()
