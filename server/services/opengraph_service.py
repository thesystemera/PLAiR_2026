from pathlib import Path
from typing import Optional

class OpenGraphService:

    def __init__(self, base_url: str = "https://plair.live"):
        self.base_url = base_url.rstrip("/")
        self._template_cache: Optional[str] = None

    def _get_base_template(self) -> str:
        if self._template_cache:
            return self._template_cache

        dist_path = Path(__file__).parent.parent.parent / "client" / "dist" / "index.html"

        if dist_path.exists():
            self._template_cache = dist_path.read_text(encoding="utf-8")
            return self._template_cache

        dev_path = Path(__file__).parent.parent.parent / "client" / "index.html"
        if dev_path.exists():
            self._template_cache = dev_path.read_text(encoding="utf-8")
            return self._template_cache

        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>PLAiR</title>
    <!-- OG_TAGS -->
</head>
<body>
    <div id="root"></div>
    <script>window.location.href = '/';</script>
</body>
</html>"""

    def generate_track_meta_tags(
        self,
        track_id: str,
        title: str,
        artist: str,
        description: Optional[str] = None,
        genre: Optional[str] = None,
    ) -> str:

        track_url = f"{self.base_url}/track/{track_id}"
        artwork_url = f"{self.base_url}/api/artwork/{track_id}"

        if not description:
            description = f'Listen to "{title}" by {artist} on PLAiR'

        tags = [
            '<meta property="og:type" content="music.song" />',
            f'<meta property="og:title" content="{self._escape_attr(title)} - {self._escape_attr(artist)}" />',
            f'<meta property="og:description" content="{self._escape_attr(description)}" />',
            f'<meta property="og:image" content="{artwork_url}" />',
            f'<meta property="og:url" content="{track_url}" />',
            '<meta property="og:site_name" content="PLAiR" />',

            '<meta name="twitter:card" content="summary_large_image" />',
            f'<meta name="twitter:title" content="{self._escape_attr(title)} - {self._escape_attr(artist)}" />',
            f'<meta name="twitter:description" content="{self._escape_attr(description)}" />',
            f'<meta name="twitter:image" content="{artwork_url}" />',

            f'<meta property="music:musician" content="{self._escape_attr(artist)}" />',
        ]

        if genre:
            tags.append(f'<meta property="music:genre" content="{self._escape_attr(genre)}" />')

        return "\n    ".join(tags)

    def render_track_page(
        self,
        track_id: str,
        title: str,
        artist: str,
        description: Optional[str] = None,
        genre: Optional[str] = None,
    ) -> str:

        template = self._get_base_template()
        meta_tags = self.generate_track_meta_tags(
            track_id=track_id,
            title=title,
            artist=artist,
            description=description,
            genre=genre,
        )

        page_title = f"{title} - {artist} | PLAiR"

        if "</head>" in template:
            template = template.replace(
                "</head>",
                f"    {meta_tags}\n    <title>{self._escape_html(page_title)}</title>\n  </head>"
            )

        import re
        template = re.sub(
            r'<title>[^<]*</title>',
            f'<title>{self._escape_html(page_title)}</title>',
            template,
            count=1
        )

        return template

    @staticmethod
    def _escape_attr(value: Optional[str]) -> str:
        if not value:
            return ""
        return (
            value
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    @staticmethod
    def _escape_html(value: Optional[str]) -> str:
        if not value:
            return ""
        return (
            value
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
