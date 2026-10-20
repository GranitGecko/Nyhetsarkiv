"""Lättviktsberikning: plats (stad/land) och ämne.

Börjar enkelt med en ordlista (noll beroenden, mycket snabbt). Byt gärna ut
mot en NER-modell (KB-BERT / spaCy) senare; gränssnittet `enrich(data)` är detsamma.
"""
import re
from collections import Counter

SWE_CITIES = [
    "Stockholm", "Göteborg", "Malmö", "Uppsala", "Västerås", "Örebro", "Linköping",
    "Helsingborg", "Jönköping", "Norrköping", "Lund", "Umeå", "Gävle", "Borås",
    "Södertälje", "Eskilstuna", "Halmstad", "Växjö", "Karlstad", "Sundsvall", "Luleå",
    "Trollhättan", "Östersund", "Borlänge", "Kristianstad", "Kalmar", "Skellefteå",
    "Falun", "Kiruna", "Visby", "Nyköping", "Karlskrona", "Varberg", "Uddevalla",
    "Täby", "Solna", "Nacka", "Huddinge", "Botkyrka", "Haparanda", "Katrineholm",
]
WORLD_CITIES = [
    "London", "Paris", "Berlin", "Moskva", "Kiev", "Kyiv", "Washington", "New York",
    "Bryssel", "Oslo", "Köpenhamn", "Helsingfors", "Peking", "Jerusalem", "Teheran",
    "Damaskus", "Istanbul", "Madrid", "Wien", "Warszawa", "Genève", "Tel Aviv",
    "Los Angeles", "Bagdad", "Kabul", "Kairo", "Tokyo", "Dubai", "Riga", "Tallinn",
]
COUNTRIES = [
    "Sverige", "Norge", "Danmark", "Finland", "Ryssland", "Ukraina", "USA", "Kina",
    "Israel", "Iran", "Tyskland", "Frankrike", "Storbritannien", "Polen", "Turkiet",
    "Syrien", "Indien", "Japan", "Spanien", "Italien", "Nederländerna", "Belgien",
    "Schweiz", "Österrike", "Estland", "Lettland", "Litauen", "Kanada", "Mexiko",
    "Brasilien", "Argentina", "Egypten", "Saudiarabien", "Sydafrika", "Nordkorea",
    "Sydkorea", "Pakistan", "Afghanistan", "Irak", "Libanon", "Ungern", "Grekland",
]


def _rx(names):
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<!\w)({alt})(?::s|s)?(?!\w)")


_CITY_RX = _rx(SWE_CITIES + WORLD_CITIES)
_COUNTRY_RX = _rx(COUNTRIES)


def enrich(data):
    text = " ".join(filter(None, [data.get("title"), data.get("ingress"),
                                  (data.get("body") or "")[:6000]]))
    cities = Counter(m.group(1) for m in _CITY_RX.finditer(text))
    countries = Counter(m.group(1) for m in _COUNTRY_RX.finditer(text))
    city = cities.most_common(1)[0][0] if cities else None
    country = countries.most_common(1)[0][0] if countries else None
    if not country and city in SWE_CITIES:
        country = "Sverige"
    names = [n for n, _ in (cities + countries).most_common()]
    tags = data.get("tags") or []
    topic = tags[0] if tags else data.get("category")
    return {"city": city, "country": country, "locations": names, "topic": topic}
