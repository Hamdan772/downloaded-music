from dataclasses import dataclass, field


@dataclass
class Track:
    title: str
    artist: str
    album: str = ""
    track_number: int = 0
    duration_ms: int = 0
    cover_url: str = ""
    isrc: str = ""
    spotify_id: str = ""
    lyrics_lrc: str = ""
    extra_artists: list = field(default_factory=list)

    @property
    def search_query(self) -> str:
        return f"{self.artist} - {self.title}"

    @property
    def filename_stem(self) -> str:
        safe = "".join(c for c in f"{self.artist} - {self.title}" if c not in '\\/:*?"<>|')
        return safe.strip()

    @property
    def album_folder_name(self) -> str:
        name = self.album.strip() or "Unknown Album"
        safe = "".join(c for c in name if c not in '\\/:*?"<>|')
        return safe.strip() or "Unknown Album"
