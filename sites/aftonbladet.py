from .base import Site


class Aftonbladet(Site):
    key = "aftonbladet"
    name = "Aftonbladet"
    home = "https://www.aftonbladet.se/"
    # OBS: verifiera med `python cli.py check-feeds`
    feeds = ["https://rss.aftonbladet.se/rss2/small/pages/sections/senastenytt/"]
    sitemaps = []
    political_label = "Oberoende socialdemokratisk"
    cdx_filters = ["original:.*/a/.+"]        # artiklar har /a/<id>/ i sökvägen
    article_url_re = r"^https://www\.aftonbladet\.se/[^/?#]+/a/[^/?#]+"

    def postprocess(self, data, html, url):
        parts = self.path_parts(url)
        if not data.get("category") and parts:
            data["category"] = parts[0]
        return data
