# Nyhetsarkivet

Samlar in, tolkar och **webbarkiverar** (WARC, som Internet Archive) svenska nyhetssajter: public service, rikstäckande och
lokala dagstidningar, nätmedier och opinionssajter över hela spektrumet, samt parti- och organisationsmedia. Resultatet blir
sökbart via ett **JSON-API** och en **webbplats i 2005 års stil** med startsida, en sida per källa (ägare och nyckelpersoner
med källhänvisningar) och en artikellista med sidnavigering. Lättviktigt: Python, SQLite, ingen React, ingen TypeScript.

**Webbplats (statisk version): https://granitgecko.github.io/Nyhetsarkiv/**

*English: a lightweight Python tool that collects, parses and web-archives (WARC) Swedish news sites, from public service and
local papers to alternative and partisan media, and serves them through a JSON API and a retro 2005-style website. Documentation
is in Swedish. See "Juridik och etik" below before using it.*

## Juridik och etik (läs först)
- **Respektera sajterna.** Verktyget följer `robots.txt`, väntar mellan anrop, identifierar sig med din kontaktadress och
  kringgår varken betalväggar, inloggning eller blockeringar. Svarar en sajt 403/406 på webbläsarfångst ska det respekteras.
- **Upphovsrätt.** Det som samlas in är i regel upphovsrättsskyddat. Arkivet är tänkt för eget bruk, forskning och dokumentation.
  **Publicera aldrig insamlad data eller WARC-filer** (mappen `data/` är undantagen i git, och `tools/check_publish.py` kontrollerar det).
  Läs även sajternas användarvillkor. Du ansvarar själv för hur du använder verktyget och för lagarna där du bor; detta är inte juridisk rådgivning.
- **Urval är inte stöd.** Att en källa finns med betyder inte att projektet står bakom eller bedömer den som tillförlitlig.
- **Uppgifter om personer.** Källprofilerna (`content/profiles.json`) nämner bara offentliga roller (ägare, utgivare, chefredaktör),
  med källa och ålder på uppgiften. De kan vara fel eller inaktuella. Rätta gärna, med en källa (se `CONTRIBUTING.md`).

## Snabbstart
```
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium           # bara för webbarkivering
python cli.py contact din@epost.se    # en gång
python cli.py add-sites sources_seed.txt
python cli.py run --limit 20
uvicorn api:app --reload              # http://127.0.0.1:8000
```
Kräver Python 3.12 eller nyare. Allt du samlar in hamnar i `data/` (ignoreras av git).

## Webbplatsen
- **Start** (`/`): kort presentation av syftet, siffror och en lista över alla källor.
- **Källor** (`/kallor`, `/kalla/<nyckel>`): en sida per källa med ägare, nyckelpersoner, hur källan beskrivs och länkar till källorna för uppgifterna.
- **Artiklar** (`/artiklar`): sök och filtrera, välj antal per sida (25/50/100/200) och navigera med första, -5, föregående, sidnummer, nästa, +5 och sista, eller hoppa direkt till en sida.
- **Fångster** (`/fangster`): webbarkiverade sidor. **API:** `/docs`.
- **Admin** (`/admin`): inloggning för att radera artiklar. Avstängd tills du sätter ett lösenord (se nedan).
Källprofiler: `python cli.py profiles` visar vilka källor som saknar profil eller bör kontrolleras. Egna rättelser läggs i `data/profiles.json`.

## Publicera på GitHub Pages
GitHub Pages kan bara visa färdiga HTML-filer, inte köra programmet (Python, databas, inloggning). Utan en statisk webbplats i
repot visar Pages i stället README-filen. Så här publicerar du webbplatsen:

1. Generera den statiska versionen från din egen databas (i projektmappen):
   ```
   python cli.py export-site --repo-url https://github.com/granitgecko/Nyhetsarkiv
   ```
   Det skapar mappen `docs/` med startsida, källöversikt och en sida per källa (ägare och nyckelpersoner med källhänvisningar).
2. Checka in och pusha: `git add docs`, `git commit -m "Uppdatera webbplatsen"`, `git push`.
3. På GitHub: **Settings, Pages, Build and deployment**. Välj *Deploy from a branch*, branch `main` och mappen **/docs**, och spara.
   Efter någon minut visas webbplatsen på adressen som GitHub anger (README-sidan är då ersatt av webbplatsen).

Kör om `export-site` och pusha när du vill uppdatera siffrorna eller profilerna.

**Vad som publiceras.** Som standard ingår inga artiklar alls, bara presentation, siffror och källprofiler. Artikeltexter, ingresser och
webbarkiv (WARC) exporteras aldrig. Med `--with-articles` läggs sidnavigerade listor med **rubrik, datum och länk till originalet** till
(markerade så att sökmotorer inte indexerar dem). Det är fortfarande insamlat material, så fundera på om du vill publicera det.
Sökning, API, inloggning och webbarkivering ingår inte i den statiska versionen; de körs lokalt med `uvicorn api:app`.
Vill du köra den dynamiska versionen på en server behövs en värd som kan köra Python, men tänk då på upphovsrätten och att adminfunktionen inte ska exponeras.

## Administration: radera artiklar
Webbplatsen är skrivskyddad för besökare. En administratör kan radera artiklar, men bara om du aktivt slagit på det:
```
python cli.py admin-password            # frågar efter ett lösenord (minst 10 tecken), en gång
python cli.py admin-password --disable  # stäng av igen (loggar ut alla)
```
Logga sedan in på `/admin/login`. Därefter syns rutor för att markera artiklar i listan, en raderaknapp på artikelsidan och en
adminsida (`/admin`) med logg och lista över raderade artiklar. Många på en gång: `python cli.py delete --site svt --before 2025-01-01`
(utan `--yes` visas bara vad som skulle raderas).

**Vad "radera" betyder.** Artikelns innehåll och sparade rå-HTML tas bort, men adressen finns kvar med status `deleted`, annars
skulle nästa körning hämta in artikeln igen. Webbarkivfilerna (WARC) är append-only och ändras inte; raderingen visar vilka filer
som fortfarande innehåller sidorna, så att du själv kan ta bort dem om det behövs. Alla raderingar loggas.

**Säkerhet.** Inga standardlösenord: utan lösenord går det inte att logga in. Lösenordet sparas som PBKDF2-hash i `data/settings.json`
(ignoreras av git). Inloggningen gäller 8 timmar (HttpOnly- och SameSite=Strict-kaka), ändringar kräver en CSRF-token och en
bekräftelsekryssruta, och efter fem felaktiga försök spärras inloggningen i en minut. Ett nytt lösenord loggar ut alla gamla sessioner.
Kör webbplatsen bara på din egen dator (`uvicorn` lyssnar som standard på 127.0.0.1). Ska andra nå den: lägg den bakom HTTPS
(med `--proxy-headers` om du använder en omvänd proxy) och överväg om adminfunktionen ska vara påslagen alls.

## Uppdatera till en ny version
Allt personligt ligger i mappen `data/`: databasen, rå-HTML, webbarkivet (WARC), `sources.json` (dina sajter) och
`settings.json` (din kontaktadress). Ska du byta till en ny version av programmet räcker det att packa upp den och
kopiera över din gamla `data/`-mapp. (Låg `sources.json` i projektmappen i en äldre version, flytta in den i `data/` först.)
Databasen uppgraderas automatiskt vid nästa körning. Skapa en ny miljö vid behov (se Kom igång) och kör sedan
`python cli.py status` för att se att allt finns kvar.

## Hjälp i terminalen
Allt går att få förklarat direkt i terminalen, på svenska och med exempel:
```
python cli.py                    # utan kommando: hela guiden med arbetsflöde och alla kommandon
python cli.py hjälp archive      # detaljerad hjälp för ett kommando (även: help archive)
python cli.py archive --help     # samma sak; -help, -h och /? fungerar också
```
Felmeddelanden är på svenska och pekar på hjälpen.

`fetch` och `parse` visar ett förlopp medan de arbetar: en rad som uppdateras med stapel, antal klara, procent, antal fel, hastighet,
beräknad tid kvar och adressen som hämtas just nu. Ctrl+C avbryter snyggt: pågående anrop slutförs, det som redan hämtats ligger
kvar och kommandot kan köras om för att fortsätta. (Går utskriften till en fil visas i stället en vanlig rad var 5:e procent.)

## Så är det uppbyggt
- **En gemensam motor, en fil per sajt.** Allt som är specifikt för en tidning ligger i `sites/<sajt>.py`
  (flöden, URL-mönster, artikeltyper, cookie-ruta). Ändrar du `sites/dn.py` påverkas inte SVT.
- **Tre lager av data, inga skärmbilder:**
  1. *Rå-HTML* för varje artikel, komprimerad i `data/raw/`.
  2. *Strukturerad data* (titel, författare, kategori, stad, ...) i SQLite för sök och API, härledd ur rå-HTML:en;
     `python cli.py parse --reparse` tolkar om allt utan ny hämtning.
  3. *Webbarkiv* (hela sidan med bilder, CSS, JavaScript) i `data/warc/` som `.warc.gz`.
- **Pipeline:** discover (RSS/sitemap) -> fetch -> parse -> enrich, och därefter archive.

## Kom igång
```
python -m venv .venv
.venv\Scripts\Activate.ps1                  # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```
1. Ange din kontaktadress (en gång): `python cli.py contact din@epost.se`. Den sparas i `data/settings.json`.
2. `python cli.py check-feeds`
3. `python cli.py run --limit 50`         (strukturerad data, snabbt)
4. `python cli.py archive --limit 20`     (fullständiga fångster, tar längre tid)
   eller båda i ett: `python cli.py all --limit 20`
5. `uvicorn api:app --reload`  ->  http://127.0.0.1:8000/  och  /docs

Kommandon: `contact`, `admin-password`, `delete`, `export-site`, `check-feeds`, `discover`, `fetch`, `parse`, `run`, `archive`, `all`, `status`, `probe`, `backfill`, `snowball`, `wayback`, `sites`, `add-site`, `add-sites`.
Alla kan begränsas: `--site dn svt`. `archive` har även `--front-only` och `--no-front`.

## Fullständig arkivering (WARC)
`python cli.py archive` gör för varje sajt, parallellt (en process per sajt):
1. fångar startsidan, och därefter varje artikel som ännu inte är arkiverad (nyaste först, upp till `--limit`),
2. laddar sidan i riktig webbläsare (Playwright) med JavaScript, scrollar för att utlösa lazy-loadade
   bilder och försöker klicka bort cookie-rutan,
3. spelar in all trafik (HTML, CSS, JS, bilder, typsnitt, JSON som sidan hämtar) och skriver den som
   WARC-poster till `data/warc/<sajt>/<datum>.warc.gz` (en fil per sajt och dag),
4. sparar en rad i tabellen `snapshots` (tid, fil, antal poster, storlek).

**Dubbletter:** samma resurs (samma URL och samma innehåll) som redan finns i dagens fil sparas som en
liten `revisit`-post. Loggor, CSS och typsnitt tar därför plats bara en gång per dag och sajt.

**Tid och plats:** räkna med 10-20 sekunder per sida (paus på 5 s mellan sidor, se `ARCHIVE_DELAY`).
Verklig storlek ser du på sidan http://127.0.0.1:8000/fangster, i `/api/stats` (fältet `snapshots`) eller
i filstorlekarna i `data/warc/`.

Inställningar finns i `archive_settings.py`: paus, maxstorlek per resurs, annons-/spårningsvärdar som
inte sparas (`BLOCK_HOSTS`), cookie-klick av/på och adress till pywb.

### Kontrollera att en sida är komplett (`verify`)
`python cli.py archive --site svt --limit 2 --no-front` skriver ut `article #<id>` för varje fångst. Sedan:
```
python cli.py verify 123          # artikel-id
python cli.py verify svt          # senaste startsidan för en sajt
```
Kommandot läser sidans HTML och CSS ur WARC-filen, listar allt de pekar på (stilmallar, skript, bilder, typsnitt, `@import`,
`url(...)`), och visar vad som saknas i arkivet **med orsak**: blockerad värd (`BLOCK_HOSTS`), för stor
(`MAX_RESOURCE_BYTES`), media som inte sparas, eller att resursen aldrig laddades. Det kontrollerar det sidan pekar på i
sin HTML och CSS; resurser som skript hämtar i efterhand syns i räkningen av innehåll men kan inte kontrolleras i förväg.
För artiklar jämförs också rubriken och brödtexten i databasen med den arkiverade sidan: den kan ligga i HTML:en,
bara i ett JSON-svar (sidan ritar texten med JavaScript) eller saknas (betalvägg, inloggning). `python cli.py verify 123 --list`
visar dessutom alla resurser i fångsten med typ, storlek och adress, och `--text` visar var texten finns och om den fanns vid
vanlig hämtning (skillnaden avslöjar betalväggar och sådant som bara visas i webbläsare).
Vanliga orsaker och åtgärder (alla i `archive_settings.py`):
- Annons- och spårningsskript saknas: `BLOCK_HOSTS = []` sparar allt, som Internet Archive.
- Stora filer saknas: höj `MAX_RESOURCE_BYTES`.
- Sena skript/bilder saknas: höj `NETWORK_IDLE_SECONDS`, `EXTRA_WAIT_SECONDS` eller `MAX_SCROLL_STEPS`.
- Favicon: Chromium utan fönster hämtar den aldrig själv, så `FETCH_FAVICON = True` (standard) hämtar sidans deklarerade ikoner.
- Typsnitt som CSS deklarerar men sidan inte använder (t.ex. fetstil-kursiv) hämtas aldrig av webbläsaren; `verify` räknar dem inte som luckor.
- Lazy-loadade bilder saknas (`loading="lazy"`, t.ex. i karuseller): `FORCE_EAGER_LOADING = True` (standard) laddar dem direkt.
- Arkivera om en redan arkiverad artikel efter ändrade inställningar: `python cli.py archive --site svt --again 2011 2020`.
`verify` räknar inte det som bara finns i `<noscript>` (hämtas inte av en webbläsare med JavaScript), inte alternativa
bildstorlekar i `<picture>`/`srcset`, inte reservformat för typsnitt (woff när woff2 finns) och inte `apple-touch-icon`: webbläsaren hämtar bara en.
- `verify` säger att allt finns men sidan ser oformaterad ut vid uppspelning: sätt `DEDUPLICATE = False` (så blir varje
  fångst fristående, utan dubblettposter, men filerna blir större) och arkivera om sidan.
- `python cli.py run` sparar bara HTML i `data/raw/`, utan CSS och JavaScript. Det är webbarkivet (`archive`) som fångar allt.

### Hålla koll och sätta gränser
`python cli.py status` visar per sajt hur många artiklar som finns, är tolkade, arkiverade, väntar på
arkivering och hur många fångster som tagits idag, samt diskanvändning och en uppskattning av vad
alla väntande skulle ta. Tre gränser hindrar att arkivet växer okontrollerat:
- `MAX_WARC_GB` (standard 20): arkiveringen stannar när `data/warc` är så stor.
- `DAILY_LIMIT_PER_SITE` (standard 300): max antal lyckade fångster per sajt och dygn.
- `--limit N` och `--minutes M` på `archive`/`all`: tak per körning, till exempel `python cli.py archive --limit 100 --minutes 60`.
Det som inte hinner med väntar till nästa körning (nyaste artiklar först).

### Gå längre tillbaka i tiden
RSS-flödena listar bara de senaste artiklarna (några tiotal till ett hundratal per sajt), så utan mer än
`discover` får du bara de senaste dagarna. Äldre artiklar hittar du på två sätt:
1. **Sitemaps.** `python cli.py probe` undersöker vilka sitemaps varje sajt har (via `robots.txt`) och hur långt
   bakåt de sträcker sig. Därefter hämtar `python cli.py backfill --since 2026-09-01 --max-sitemaps 30` artikel-URL:er
   från dem. Datumet kommer från sitemapens `lastmod`, som är ändringsdatum och inte alltid publiceringsdatum.
2. **Länkar i det du redan har.** `python cli.py snowball` läser den sparade rå-HTML:en och lägger till artikellänkar
   (relaterade artiklar, "läs också") som saknas. Inga nya nätanrop, och det går bakåt steg för steg.
Sedan körs vanliga kedjan: `fetch`, `parse` och `archive`, som alltid tar nyast först. Nytt som kommer in via RSS
går alltså före äldre efterhängare, och dagsgränsen och diskgränsen gäller även för dem.
`python cli.py status` visar hur mycket som väntar. Sajter som saknar bra sitemaps får fylla på bakåt via snowball.

### Långt bakåt: Wayback Machines katalog
Sitemaps och länkar räcker sällan långt. Internet Archives katalog (CDX) kan lista vilka artikel-URL:er de har
sett under en viss tid. Vi använder den bara som URL-lista; själva sidorna hämtas från sajterna.
```
python cli.py wayback --since 2025-01 --until 2025-03 --max 5000     # lägg URL:er i kön
python cli.py fetch --since 2025-01-01 --until 2025-04-01 --limit 500   # hämta rå-HTML
python cli.py parse
python cli.py archive --since 2025-01-01 --until 2025-04-01 --limit 100 --no-front   # WARC (valfritt)
```
Ta ett tidsfönster i taget (en månad eller ett kvartal). Fönstret gäller samma datum i alla stegen: när adressen hittades (för äldre artiklar: när Internet Archive
såg sidan), annars publiceringsdatum. Använd alltså samma `--since/--until` i `fetch` och `archive`. Katalogens svar sorteras alfabetiskt, inte efter datum;
om `wayback` skriver "fler finns" ska du smalna av fönstret, annars får du ett slumpmässigt urval.
Att tänka på:
- Datum i `wayback` är när Internet Archive såg sidan, inte när artikeln publicerades. Publiceringsdatumet sätts
  från artikeln själv när den tolkas.
- Gamla sidor hämtas som de ser ut **idag**: de kan vara borttagna (404, markeras som fel), omskrivna eller
  bakom betalvägg. WARC-filen får därför dagens datum som fångsttid, inte det gamla.
- Mängd: 100 000 artiklar är ungefär 55 timmar hämtning per sajt vid 2 sekunders paus, och i storleksordningen
  3-6 GB rå-HTML och databas (grov uppskattning, kolla `status`). Full WARC-arkivering av lika många blir
  tiotals GB; höj i så fall `DAILY_LIMIT_PER_SITE` och `MAX_WARC_GB` medvetet i `archive_settings.py`.
- Ett rimligt upplägg: rå-HTML och tolkning för hela historiken, och WARC för de perioder du bryr dig mest om.

### Spela upp ett arkiv
- **ReplayWeb.page (enklast, ingen installation):** öppna https://replayweb.page, välj din `.warc.gz`
  från datorn (filen laddas inte upp, den öppnas lokalt i webbläsaren) och sök fram sidan.
- **pywb (egen server):** `pip install pywb`, sedan
  ```
  wb-manager init nyhetsarkivet
  wb-manager add nyhetsarkivet data\warc\svt\2026-10-10.warc.gz
  wayback
  ```
  Öppna http://localhost:8080/nyhetsarkivet/. Länken "pywb" på varje artikelsida i webbplatsen pekar dit.
  Filer växer under dagen; lägg till dem igen (eller `wb-manager reindex nyhetsarkivet`) för nya poster.

### Begränsningar att känna till
- Du får det en vanlig besökare ser. DN:s betalvägg arkiveras alltså som betalvägg.
- Videor och ljud, resurser över `MAX_RESOURCE_BYTES`, POST-anrop och spårningsvärdar sparas inte.
  Annonser syns därför inte vid uppspelning.
- **DN:** svarar HTTP 406 på webbläsarfångster (vanlig hämtning fungerar). Webbarkiveringen är därför avstängd
  för DN (`archive_enabled = False` i `sites/dn.py`); rå-HTML och strukturerad data hämtas som vanligt.
- Cookie-rutor: ett generellt försök klickar på knappar som "Godkänn alla"/"Acceptera alla". Fungerar det
  inte på en sajt, åsidosätt `dismiss_consent(self, page)` i just den sajtens fil. Observera att ett
  klick på "godkänn" också godkänner spårning i den webbläsarsessionen.
- Kör inte två `archive` mot samma sajt samtidigt; ett lås hindrar det (och rensas efter 3 timmar).
- Att arkivera för eget bruk är en sak, att publicera arkiverade sidor öppet en annan (upphovsrätt).

## Schemaläggning (Windows)
Aktivitetsschemaläggaren (Task Scheduler), åtgärd "Starta ett program":
- Program: `C:\...\nyhetsarkivet\.venv\Scripts\python.exe`
- Argument: `cli.py all --limit 30`  och  "Starta i": projektmappen
Låt den köras var 30:e minut. Kör `all`, så hämtas nya artiklar först och arkiveras sedan.

## API
- `GET /api/articles?source=&category=&author=&city=&country=&article_type=&topic=&q=&from=&to=&limit=&offset=`
- `GET /api/articles/{id}` (med `snapshots`), `GET /api/snapshots`, `GET /api/sources`, `GET /api/stats`
- WARC-filer kan laddas ned från `/filer/warc/<sajt>/<datum>.warc.gz`

## Fler källor: nätmedier och lokaltidningar
Tre färdiga listor: `sources_seed.txt` (nationella och regionala tidningar) och `sources_seed_alternativ.txt` (nätmedier, opinion, parti- och organisationsmedia).
Utöver SVT, DN och Aftonbladet (egna filer i `sites/`) kan vilken sajt som helst läggas till **utan kod**, via
`data/sources.json`. Verktyget hittar RSS-flöden åt dig:
```
python cli.py add-site https://www.nyheteridag.se              # en sajt
python cli.py add-sites sources_seed.txt                        # många på en gång (en adress per rad)
python cli.py add-site https://www.svd.se --name "Svenska Dagbladet" --feed https://www.svd.se/feed/articles.rss   # flödet angivet för hand
python cli.py sites                                             # lista alla sajter
python cli.py check-feeds --site nyheteridag                    # kontrollera flödet
python cli.py run --site nyheteridag --limit 20
```
`add-site` läser startsidan (om `robots.txt` tillåter), letar efter `<link rel="alternate">` och vanliga
flödesadresser, kontrollerar att flödet har minst tre artiklar på sajten, och skriver en post i `data/sources.json`.
Sajter utan fungerande flöde läggs inte till, och felet visas. `sources_seed.txt` är en utgångslista; adresserna är
skrivna från minnet och inte kontrollerade, så kommandot rapporterar vilka som inte gick att nå.

En post i `sources.json` kan se ut så här (alla fält utom `key` och `home` är valfria):
```
{"key": "nyheteridag", "name": "Nyheter Idag", "home": "https://www.nyheteridag.se/",
 "feeds": ["https://www.nyheteridag.se/feed/"],
 "political_label": null, "daily_limit": 50, "archive_enabled": true,
 "article_regex": null, "group": "nätmedia"}
```
- `daily_limit`: egen dygnsgräns för WARC-arkivering av just den sajten (annars `DAILY_LIMIT_PER_SITE`).
- `archive_enabled: false`: bara rå-HTML och tolkning, ingen webbarkivering.
- `article_regex`: eget mönster för vad som är en artikel-URL, om standardreglerna missar.
- `political_label`: sätts bara om sajten själv publicerar en etikett. Nya sajter får ingen etikett automatiskt.
Sajter utan RSS kan använda listsidor i stället: `--index <adress>` läser artikellänkar från t.ex. en sektionssida (och `--article-regex` anger hur en artikeladress ser ut).
Allt som står i en sajts eget RSS-flöde räknas som artikel. För sitemaps, Wayback och länkföljning används
striktare regler (sektions- och taggsidor sorteras bort).

**Många sajter:** alla delar disk- och dygnsgränserna. Med tiotals sajter bör du sänka `daily_limit` för de
mindre (till exempel 20-50) och titta på `python cli.py status` efter första körningen. Högst `MAX_PARALLEL` sajter
(standard 4) arkiveras samtidigt, och varje sajt får sin egen paus mellan sidor. Använd `--site` för att köra en del
av sajterna åt gången.

## Lägga till en ny sajt med egen kod
Behövs bara när en sajt kräver särskild tolkning.
1. Kopiera `sites/svt.py` till `sites/<ny>.py`, ändra nyckel, flöden och `article_url_re`.
2. Registrera den i `sites/__init__.py`.
3. `python cli.py check-feeds --site <ny>`, sedan `python cli.py all --site <ny> --limit 5`.

## Fält
Direkt från sidan: titel, ingress, brödtext, författare, publicerad/uppdaterad, kategori, taggar (om
sajten publicerar dem), bild, språk, betalvägg.
Härlett: stad och land (ordlista i `enrich.py`), ämne (första tagg, annars kategori), artikeltyp (URL-mönster
och schema.org-typ). `source_political_label` är tidningens egen publicerade etikett, inte en bedömning av
artikeln.

## Skala upp senare
- SQLite räcker länge i WAL-läge. Byt till Postgres när flera maskiner skriver samtidigt.
- Fulltextsök (SQLite FTS5) i stället för `LIKE`.
- Byt ordlistan i `enrich.py` mot KB-BERT/spaCy.
- Flytta `data/warc/` till större disk eller objektlagring när den växer.

## Test
`python -m unittest discover -s tests -v` (körs även automatiskt på GitHub vid varje ändring)

## Licens
MIT, se `LICENSE`. Källprofilerna och övrigt innehåll i `content/` följer samma licens.
