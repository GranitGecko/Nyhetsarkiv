# Bidra

Bidrag är välkomna: fler källor, rättelser i källprofiler, buggrättningar och bättre tolkning av enskilda sajter.

## Köra projektet och testerna
```
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
python tools/check_publish.py
```
Kräver Python 3.12 eller nyare. Testerna behöver varken internet eller en webbläsare.

## Lägga till en källa
```
python cli.py add-site https://www.exempel.se                       # hittar RSS-flödet själv
python cli.py add-site https://www.exempel.se --feed https://www.exempel.se/rss    # flödet angivet för hand
python cli.py add-site https://www.exempel.se --index https://www.exempel.se/nyheter --article-regex '...'   # utan RSS
```
Lägg gärna sajten i en av `sources_seed*.txt` och, om du kan, en profil i `content/profiles.json`.

## Rätta eller lägga till en källprofil
Profilerna i `content/profiles.json` beskriver ägare, nyckelpersoner och hur en källa beskrivs. Regler:

1. **Varje uppgift om en person ska ha en källa** (länk) och uppgiftens ålder ska framgå, till exempel "(2024)".
2. Bara **offentliga roller** (ägare, vd, chefredaktör, ansvarig utgivare, grundare). Inga privata uppgifter.
3. Föredra primärkällor (sajtens egen om-oss-sida, myndighetsregister, bolagsregister) och etablerade medier. Undvik anonyma
   sammanställningar och sajter som visar personuppgifter från register.
4. **Beskriv, värdera inte.** Skriv vem som säger vad ("Sveriges Radio (2015): ...") i stället för egna omdömen.
5. Osäkert eller ofullständigt? Sätt fältet `verify` med vad som behöver kontrolleras. Hellre en tom ruta än en gissning.

`python cli.py profiles` visar vad som saknas och om datan har fel. Testerna kräver att alla personuppgifter har en källa.

## Innan du skickar in
- Kör testerna och `python tools/check_publish.py`.
- Checka aldrig in insamlad data (`data/`), databaser eller WARC-filer.
