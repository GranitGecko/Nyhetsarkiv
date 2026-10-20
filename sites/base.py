"""Basklass för en nyhetssajt. En sajt = en liten fil som ärver härifrån."""
import re
from urllib.parse import urlsplit

from parse_base import parse_generic

# Gemensamma regler för artikeltyp utifrån URL-sökväg. Sajter kan lägga till egna.
GENERIC_TYPE_RULES = [
    (r"/(ledare|ledarsidan)(/|$)", "ledare"),
    (r"/(debatt|opinion|asikt|åsikt)(/|$)", "debatt"),
    (r"/(kronika|krönika|kolumn|kolumnister)(/|$)", "krönika"),
    (r"/(recension|recensioner)(/|$)", "recension"),
    (r"/(webb-?tv|play|video)(/|$)", "video"),
    (r"/(live|direkt)(/|$)", "livebevakning"),
]


class Site:
    key = ""
    name = ""
    home = ""
    feeds = []
    sitemaps = []
    political_label = None        # sajtens egen publicerade etikett, inte en bedömning av enskilda artiklar
    article_url_re = None
    type_rules = []               # sajtspecifika (regex, typ), prövas före de generella
    archive_enabled = True        # False = ingen webbarkivering för sajten (hämtning/tolkning fortsätter)
    index_pages = []              # listsidor (t.ex. sektionssidor) att läsa artikellänkar från när RSS saknas
    cdx_prefixes = None           # prefix att fråga Wayback-katalogen om; None = hela sajten
    cdx_filters = []              # extra serverfilter, t.ex. 'original:.*/a/.+'
    daily_limit = None            # egen dygnsgräns för webbarkivering; None = DAILY_LIMIT_PER_SITE
    archive_delay = None          # sekunder mellan sidfångster; None = ARCHIVE_DELAY i archive_settings.py

    def is_article_url(self, url):
        return bool(self.article_url_re and re.search(self.article_url_re, url))

    def is_feed_article(self, url):
        """Är URL:en en artikel när den kommer från sajtens eget RSS-flöde?"""
        return self.is_article_url(url)

    def article_type(self, url, data):
        path = urlsplit(url).path.lower()
        for pattern, label in self.type_rules + GENERIC_TYPE_RULES:
            if re.search(pattern, path):
                return label
        ld = (data.get("ld_type") or "")
        if "Opinion" in ld:
            return "debatt"
        if "Reportage" in ld:
            return "reportage"
        if "Review" in ld:
            return "recension"
        if "LiveBlog" in ld:
            return "livebevakning"
        if "Analysis" in ld:
            return "analys"
        return "nyhet"

    def dismiss_consent(self, page):
        """Klicka bort cookie-ruta vid arkivering. Åsidosätt i sajtens fil om den generiska inte räcker.
        Returnera True om något klickades."""
        return False

    def postprocess(self, data, html, url):
        """Sajtspecifika justeringar. Åsidosätt i sajtens fil vid behov."""
        return data

    def parse(self, html, url):
        data = parse_generic(html, url)
        data = self.postprocess(data, html, url)
        data["article_type"] = self.article_type(url, data)
        data["source_political_label"] = self.political_label
        return data

    @staticmethod
    def path_parts(url):
        return [p for p in urlsplit(url).path.split("/") if p]
