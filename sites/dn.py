from .base import Site


class Dn(Site):
    key = "dn"
    name = "Dagens Nyheter"
    home = "https://www.dn.se/"
    # OBS: verifiera med `python cli.py check-feeds`
    feeds = ["https://www.dn.se/rss/"]
    sitemaps = []
    political_label = "Oberoende liberal"
    # DN svarar HTTP 406 på webbläsarfångster även om vanlig hämtning fungerar (200).
    # Webbarkivering är därför avstängd; hämtning och tolkning av rå-HTML fortsätter.
    archive_enabled = False
    article_url_re = r"^https://www\.dn\.se/[^/?#]+/[^/?#]+/?$"
    type_rules = [(r"^/(ledare|ledarsidan)(/|$)", "ledare")]

    def is_article_url(self, url):
        parts = self.path_parts(url)
        if not parts or parts[0] in {"rss", "om-dn", "prenumerera", "sok", "tjanster"}:
            return False
        return super().is_article_url(url)

    def postprocess(self, data, html, url):
        parts = self.path_parts(url)
        if not data.get("category") and parts:
            data["category"] = parts[0]
        return data
