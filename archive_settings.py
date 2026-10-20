"""Inställningar för fullständig arkivering. Ändra här, inte i koden."""
from config import DATA

WARC_DIR = DATA / "warc"
TMP_DIR = DATA / "tmp"

# --- Gränser så att arkivet inte växer okontrollerat ---
MAX_WARC_GB = 20                       # arkiveringen stannar när data/warc är så här stor
DAILY_LIMIT_PER_SITE = 300             # max lyckade fångster per sajt och dygn (UTC)
# Dessutom: --limit (antal per körning) och --minutes (tidsgräns per körning) på kommandoraden.

MAX_PARALLEL = 4                       # högst så här många sajter arkiveras samtidigt (en Chromium per sajt)

# True: resurser som redan finns i dagens fil sparas bara som en liten 'revisit'-post (sparar mycket plats).
# False: varje fångst sparar allt i full form och blir fristående, men filerna blir större.
# Sätt False om en sida ser oformaterad ut vid uppspelning trots att `verify` säger att allt finns.
DEDUPLICATE = True

# True: bilder och ramar med loading="lazy" (och data-src/data-srcset) laddas direkt, även om de ligger utanför
# bild­området, t.ex. i sidscrollande karuseller. Annars missas de om scrollningen aldrig når dem.
FORCE_EAGER_LOADING = True

# True: hämta sidans deklarerade ikoner (favicon). Chromium utan fönster hämtar dem annars aldrig.
FETCH_FAVICON = True

# --- Hur länge webbläsaren får ladda en sida innan den sparas ---
NETWORK_IDLE_SECONDS = 15              # vänta så här länge på att nätverket blir tyst
EXTRA_WAIT_SECONDS = 2                 # och så här länge till, för sena skript och bilder
MAX_SCROLL_STEPS = 60                  # scrolla högst så här många skärmhöjder (utlöser lazy-loadade bilder)

ARCHIVE_DELAY = 5.0                    # sekunder mellan två sidfångster mot samma sajt
MAX_RESOURCE_BYTES = 15 * 1024 * 1024  # enskilda resurser större än så sparas inte (t.ex. video)
CLICK_CONSENT = True                   # försök klicka bort cookie-rutor innan fångst
PYWB_URL = "http://localhost:8080/nyhetsarkivet"   # där pywb (om du kör det) spelar upp arkivet

# Resurser från dessa värdar sparas inte i WARC-filen (annons- och spårningsskript).
# De laddas ändå av webbläsaren under fångsten, men tar inte plats i arkivet.
# Vill du spara ALLT, som Internet Archive: sätt BLOCK_HOSTS = [] (och höj gärna MAX_RESOURCE_BYTES).
BLOCK_HOSTS = [
    "doubleclick.net", "googlesyndication.com", "googletagmanager.com", "google-analytics.com",
    "googletagservices.com", "adnxs.com", "facebook.net", "scorecardresearch.com", "taboola.com",
    "outbrain.com", "criteo.", "hotjar.com", "quantserve.com", "chartbeat.", "adsrvr.org",
    "amazon-adsystem.com", "pubmatic.com", "rubiconproject.com", "smartadserver.com",
]
