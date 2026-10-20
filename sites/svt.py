from .base import Site


class Svt(Site):
    key = "svt"
    name = "SVT Nyheter"
    home = "https://www.svt.se/nyheter/"
    # OBS: verifiera med `python cli.py check-feeds`
    feeds = ["https://www.svt.se/nyheter/rss.xml"]
    sitemaps = []
    political_label = "Obunden (public service)"
    cdx_prefixes = ["www.svt.se/nyheter/", "www.svt.se/sport/", "www.svt.se/kultur/", "www.svt.se/opinion/"]
    article_url_re = r"^https://www\.svt\.se/(nyheter|sport|kultur|opinion)/[^/?#]+/[^/?#]+"

    def postprocess(self, data, html, url):
        parts = self.path_parts(url)
        if not data.get("category") and len(parts) >= 2:
            data["category"] = parts[1]
        return data
