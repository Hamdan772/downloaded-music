"""Smoke tests for the Tk GUI (skipped where Tk or a display isn't available)."""
import pytest

tk = pytest.importorskip("tkinter")

from melograb import env, gui, pipeline  # noqa: E402
from melograb.track import Collection, Track  # noqa: E402


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(env, "environment_warnings", lambda: [])
    monkeypatch.setattr(env, "ytdlp_update_warning", lambda latest=None: "")
    monkeypatch.setattr(env, "latest_app_release", lambda repo: {})
    monkeypatch.setattr(env, "has_ffmpeg", lambda: True)
    monkeypatch.setattr(gui, "_system_prefers_dark", lambda: False)
    try:
        instance = gui.MelograbApp()
    except tk.TclError as e:
        pytest.skip(f"no display: {e}")
    instance.withdraw()
    instance.output_var.set(str(tmp_path / "out"))
    yield instance
    instance.destroy()


def load(app, n=4):
    tracks = [Track(f"Song {i}", f"Artist {i}", album="LP", duration_ms=60_000 * (i + 1)) for i in range(n)]
    app._load_tracks(Collection("playlist", "Mix", "", tracks))
    return tracks


def drain(app):
    app.update()
    try:
        while True:
            msg = app.msg_queue.get_nowait()
            getattr(app, f"_on_msg_{msg[0]}")(*msg[1:])
    except Exception:  # queue.Empty
        pass


def test_selection_filter_and_layout(app):
    load(app)
    assert app.download_btn.cget("text") == "Download All"
    app._toggle(1)
    assert app.download_btn.cget("text") == "Download 3 Selected"
    app.filter_var.set("song 2")
    assert app.tree.get_children() == ("2",)
    app.filter_var.set("")
    assert len(app.tree.get_children()) == 4
    assert app.subtitle_label.cget("text") == "4 tracks · 10 min"
    app.layout_var.set("Flat")
    app._on_layout_selected()
    assert app.layout_preview_var.get() == "→ Artist 0 - Song 0.mp3"


def test_download_selected_and_failure_detail(app, monkeypatch):
    tracks = load(app, 3)
    app._toggle(0)
    ran = []

    def fake_run(subset, out_dir, options, on_start, on_result, cancel):
        ran.extend(t.title for t in subset)
        for i, t in enumerate(subset):
            r = (pipeline.Result(t, error="video is unavailable") if i == 0
                 else pipeline.Result(t, path="/x.mp3", status="done", match_title="A - B"))
            on_result(i, r)

    monkeypatch.setattr(pipeline, "run", fake_run)
    monkeypatch.setattr(pipeline, "write_m3u", lambda *a, **k: None)
    app.on_download()
    app.after(300, app.quit)
    app.mainloop()
    drain(app)
    assert ran == ["Song 1", "Song 2"]
    assert app.results[1].status == "failed" and not app.busy
    app.tree.selection_set("1")
    app._on_select()
    assert "video is unavailable" in app.detail_var.get()
    assert app.retry_btn.winfo_manager() == "pack"
    assert tracks[0].title not in ran


def test_bad_template_blocks_download(app, monkeypatch):
    load(app, 1)
    app.template_var.set("{nope} {title}")
    errors = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda title, msg: errors.append(msg))
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: pytest.fail("should not run"))
    app.on_download()
    assert errors and "unknown field" in errors[0] and not app.busy


def test_update_banners(app):
    app._on_msg_banner("ytdlp", "yt-dlp is old", "Update now", "update_ytdlp")
    app._on_msg_banner("app", "melograb 9.9 is available", "Download", ("open_url", "https://example.invalid"))
    assert set(app.banner_rows) == {"ytdlp", "app"}
    app._remove_banner("ytdlp")
    app._remove_banner("app")
    assert not app.banner_rows and not app.banner.winfo_ismapped()


def test_dropped_csv_is_fetched(app, tmp_path, monkeypatch):
    csv = tmp_path / "list.csv"
    csv.write_text("Title,Artist\nHusk,Men I Trust\n")
    app._on_open_documents(str(csv), "/ignored.txt")
    app.after(400, app.quit)
    app.mainloop()
    drain(app)
    assert [t.title for t in app.tracks] == ["Husk"] and not app.empty_label.winfo_ismapped()
