"""Entry point for the packaged macOS app (and `python run_gui.py`).

`--self-test [--online]` runs diagnostics headlessly instead of opening the
window -- the release build runs it on the finished app.
"""
import sys

from melograb import ytdlp_updates

# Must run before anything imports yt_dlp: prefer a newer yt-dlp the user
# downloaded via "Update yt-dlp" over the one bundled in the app.
ytdlp_updates.activate()

if __name__ == "__main__":
    if "--self-test" in sys.argv:
        from melograb import selftest

        sys.exit(selftest.main([a for a in sys.argv[1:] if a != "--self-test"]))

    from melograb.gui import main

    main()
