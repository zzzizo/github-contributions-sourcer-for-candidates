"""Any place a recruiter types ("Toronto", "Brazil", "LATAM", "Austin, TX") turned into the words GitHub profiles use,
so a filter for Brazil keeps "São Paulo" and one for Texas keeps "Austin, TX".

Everything is built in: no geocoding service. A place that isn't in these lists still works; it's matched as written.
"""

import re
import unicodedata
from functools import lru_cache
from itertools import zip_longest

# -- data ---------------------------------------------------------------------
# One place per line: "Name | other names | places inside it". Other names are matched in profiles too, except
# ~name (only for typing: too short or ambiguous to look for, like "de" for Germany) and =xx (a postal code, matched
# only after a comma, as in "Austin, TX"). Places inside are cities or other places from these lists. City names
# shared by two well-known places (Cambridge, Birmingham, Portland in Maine) are left out of the smaller one.

REGIONS = """
Europe | eu, european union, ~continental europe | United Kingdom, Germany, France, Netherlands, Spain, Ireland, \
Switzerland, Sweden, Poland, Portugal, Italy, Belgium, Luxembourg, Austria, Norway, Denmark, Finland, Iceland, Estonia, \
Latvia, Lithuania, Czechia, Slovakia, Hungary, Romania, Bulgaria, Greece, Croatia, Serbia, Slovenia, Ukraine, Cyprus, \
Malta, Bosnia and Herzegovina, North Macedonia, Albania, Montenegro, Moldova
Nordics | nordic, ~nordic countries, scandinavia, ~nordic region | Sweden, Norway, Denmark, Finland, Iceland
DACH | ~d-a-ch | Germany, Austria, Switzerland
Benelux | | Netherlands, Belgium, Luxembourg
Baltics | ~baltic, ~baltic states | Estonia, Latvia, Lithuania
Iberia | ~iberian peninsula | Spain, Portugal
Eastern Europe | ~cee, ~central and eastern europe, ~central europe | Poland, Ukraine, Romania, Czechia, Hungary, \
Slovakia, Bulgaria, Serbia, Croatia, Slovenia, Estonia, Latvia, Lithuania, Moldova
Balkans | ~western balkans | Serbia, Croatia, Bosnia and Herzegovina, North Macedonia, Albania, Montenegro, Slovenia, \
Bulgaria
North America | ~na | United States, Canada, Mexico
LATAM | latin america, latinoamerica | Brazil, Mexico, Argentina, Colombia, Chile, Peru, Uruguay, Costa Rica, Ecuador, \
Bolivia, Paraguay, Venezuela, Guatemala, Panama, Dominican Republic, Puerto Rico, Honduras, El Salvador, Nicaragua
South America | | Brazil, Argentina, Colombia, Chile, Peru, Uruguay, Ecuador, Bolivia, Paraguay, Venezuela
Central America | | Costa Rica, Guatemala, Panama, Honduras, El Salvador, Nicaragua
East Asia | | Japan, South Korea, China, Taiwan, Hong Kong
Southeast Asia | asean, ~sea region | Singapore, Vietnam, Indonesia, Philippines, Malaysia, Thailand, Cambodia, Myanmar
South Asia | | India, Pakistan, Bangladesh, Sri Lanka, Nepal
Asia | | India, China, Japan, Singapore, South Korea, Taiwan, Hong Kong, Vietnam, Indonesia, Philippines, Malaysia, \
Thailand, Pakistan, Bangladesh, Sri Lanka, Nepal, Cambodia, Myanmar, Kazakhstan, Uzbekistan
Oceania | anz, australasia, ~australia and new zealand | Australia, New Zealand
APAC | asia pacific | Asia, Oceania
Middle East | | United Arab Emirates, Israel, Saudi Arabia, Qatar, Turkey, Egypt, Jordan, Lebanon, Bahrain, Kuwait, Oman, Iraq
GCC | ~gulf, ~the gulf, ~gulf countries | United Arab Emirates, Saudi Arabia, Qatar, Bahrain, Kuwait, Oman
Africa | | South Africa, Nigeria, Kenya, Egypt, Ghana, Morocco, Rwanda, Ethiopia, Uganda, Tanzania, Tunisia, Senegal, \
Algeria, Cameroon, Zimbabwe, Zambia
MENA | ~middle east and north africa | Middle East, Morocco, Tunisia, Algeria
EMEA | | Europe, Middle East, Africa
Caucasus | | Georgia (country), Armenia
Pacific Northwest | pnw | Washington State, Oregon, British Columbia
"""

COUNTRIES = """
United States | usa, us, united states of america, ~america | SF Bay Area, New York City, Seattle, Los Angeles, Boston, \
Austin, Washington DC
Canada | | Toronto, Vancouver, Montreal
Mexico | | Mexico City, Guadalajara, Monterrey, Querétaro, Puebla, Tijuana, Mérida, León
Brazil | brasil | São Paulo, Rio de Janeiro, Belo Horizonte, Florianópolis, Curitiba, Porto Alegre, Campinas, Recife, \
Brasília, Fortaleza, Goiânia
Argentina | | Buenos Aires, Rosario, Mendoza, La Plata
Chile | | Santiago, Valparaíso, Concepción
Colombia | | Bogotá, Medellín, Cali, Barranquilla, Bucaramanga
Peru | | Lima, Arequipa
Uruguay | | Montevideo
Ecuador | | Quito, Guayaquil
Bolivia | | La Paz, Cochabamba
Paraguay | | Asunción
Venezuela | | Caracas
Costa Rica | | Heredia
Guatemala | | Guatemala City
Panama | | Panama City
Dominican Republic | | Santo Domingo
Puerto Rico | | San Juan
Honduras | | Tegucigalpa
El Salvador | | San Salvador
Nicaragua | | Managua
United Kingdom | uk, great britain, britain, england, scotland, wales, northern ireland, ~gb | London, Manchester, \
Cambridge, Edinburgh, Oxford, Bristol, Leeds, Glasgow, Birmingham, Belfast, Cardiff, Brighton, Newcastle, Sheffield, \
Liverpool, Nottingham, Reading, Bath
Ireland | eire | Dublin, Cork, Galway, Limerick
Germany | deutschland, ~de | Berlin, Munich, Hamburg, Frankfurt, Cologne, Stuttgart, Düsseldorf, Leipzig, Dresden, \
Karlsruhe, Nuremberg, Hanover, Bonn, Heidelberg, Aachen, Darmstadt, Freiburg, Mannheim, Potsdam, Bremen, Dortmund, Essen
France | ~fr | Paris, Lyon, Toulouse, Marseille, Bordeaux, Nantes, Lille, Grenoble, Montpellier, Rennes, Strasbourg, \
Sophia Antipolis, Nice
Netherlands | nederland, holland, the netherlands, ~nl | Amsterdam, Rotterdam, Utrecht, The Hague, Eindhoven, Delft, \
Leiden, Groningen
Belgium | belgique, belgie | Brussels, Antwerp, Ghent, Leuven, Liège
Luxembourg | | Luxembourg
Switzerland | schweiz, suisse, svizzera, ~ch | Zurich, Geneva, Lausanne, Basel, Bern, Lugano, Zug
Austria | osterreich | Vienna, Graz, Linz, Salzburg, Innsbruck
Spain | espana, ~es | Madrid, Barcelona, Valencia, Seville, Málaga, Bilbao, Zaragoza, Alicante, Palma, Granada
Portugal | ~pt | Lisbon, Porto, Braga, Coimbra, Aveiro, Faro
Italy | italia, ~it | Milan, Rome, Turin, Bologna, Florence, Naples, Pisa, Padua, Genoa, Trento, Venice
Sweden | sverige, ~se | Stockholm, Gothenburg, Malmö, Uppsala, Lund, Linköping
Norway | norge | Oslo, Bergen, Trondheim, Stavanger
Denmark | danmark, ~dk | Copenhagen, Aarhus, Odense, Aalborg
Finland | suomi, ~fi | Helsinki, Espoo, Tampere, Oulu, Turku
Iceland | island | Reykjavík
Estonia | eesti | Tallinn, Tartu
Latvia | latvija | Riga
Lithuania | lietuva | Vilnius, Kaunas
Poland | polska, ~pl | Warsaw, Kraków, Wrocław, Gdańsk, Poznań, Łódź, Katowice, Lublin, Szczecin
Czechia | czech republic, cesko | Prague, Brno, Ostrava, Plzeň
Slovakia | slovensko | Bratislava, Košice
Hungary | magyarorszag | Budapest, Debrecen, Szeged
Romania | | Bucharest, Cluj-Napoca, Iași, Timișoara, Brașov
Bulgaria | | Sofia, Plovdiv, Varna
Greece | hellas | Athens, Thessaloniki, Heraklion, Patras
Croatia | hrvatska | Zagreb, Split, Rijeka
Serbia | srbija | Belgrade, Novi Sad, Niš
Slovenia | slovenija | Ljubljana, Maribor
Bosnia and Herzegovina | bosnia, ~bih | Sarajevo
North Macedonia | macedonia | Skopje
Albania | | Tirana
Montenegro | | Podgorica
Moldova | | Chișinău
Ukraine | | Kyiv, Lviv, Kharkiv, Odesa, Dnipro, Zaporizhzhia, Vinnytsia
Belarus | | Minsk
Russia | russian federation | Moscow, Saint Petersburg, Novosibirsk, Kazan, Yekaterinburg, Nizhny Novgorod
Turkey | turkiye | Istanbul, Ankara, Izmir
Cyprus | | Limassol, Nicosia, Larnaca
Malta | | Valletta, Sliema
Georgia (country) | ~sakartvelo | Tbilisi, Batumi
Armenia | | Yerevan
Kazakhstan | | Almaty, Astana
Uzbekistan | | Tashkent
Israel | | Tel Aviv, Jerusalem, Haifa, Herzliya, Ramat Gan, Petah Tikva
United Arab Emirates | uae, emirates | Dubai, Abu Dhabi, Sharjah
Saudi Arabia | ksa, ~saudi | Riyadh, Jeddah, Dammam, Khobar
Qatar | | Doha
Bahrain | | Manama
Kuwait | | Kuwait City
Oman | | Muscat
Jordan | | Amman
Lebanon | | Beirut
Iraq | | Baghdad, Erbil
Iran | | Tehran, Isfahan, Shiraz
Egypt | | Cairo, Alexandria, Giza
Morocco | maroc | Casablanca, Rabat, Marrakech, Tangier
Tunisia | tunisie | Tunis, Sfax, Sousse
Algeria | algerie | Algiers, Oran
Nigeria | | Lagos, Abuja, Ibadan, Port Harcourt
Kenya | | Nairobi, Mombasa
Ghana | | Accra, Kumasi
South Africa | rsa, ~za | Cape Town, Johannesburg, Pretoria, Durban, Stellenbosch
Ethiopia | | Addis Ababa
Rwanda | | Kigali
Uganda | | Kampala
Tanzania | | Dar es Salaam, Arusha
Senegal | | Dakar
Cameroon | cameroun | Douala, Yaoundé
Zimbabwe | | Harare
Zambia | | Lusaka
India | bharat, ~in | Bengaluru, Hyderabad, Pune, Delhi NCR, Mumbai, Chennai
Pakistan | | Karachi, Lahore, Islamabad, Rawalpindi, Faisalabad, Peshawar
Bangladesh | | Dhaka, Chattogram, Sylhet
Sri Lanka | | Colombo, Kandy
Nepal | | Kathmandu, Lalitpur, Pokhara
China | prc, ~mainland china | Beijing, Shanghai, Shenzhen, Hangzhou, Guangzhou, Chengdu, Wuhan, Nanjing, Xi'an, Suzhou
Hong Kong | hk, hksar | Kowloon
Taiwan | | Taipei, Hsinchu, Taichung, Kaohsiung, Tainan
Japan | nippon, ~jp | Tokyo, Osaka, Kyoto, Yokohama, Fukuoka, Nagoya, Sapporo
South Korea | korea, republic of korea, ~kr | Seoul, Busan, Pangyo, Seongnam, Daejeon, Incheon
Singapore | ~sg |
Malaysia | | Kuala Lumpur, Penang, Petaling Jaya, Johor Bahru, Cyberjaya
Indonesia | | Jakarta, Bandung, Surabaya, Yogyakarta, Bali, Denpasar, Malang
Thailand | | Bangkok, Chiang Mai, Phuket
Vietnam | viet nam | Ho Chi Minh City, Hanoi, Da Nang
Philippines | | Manila, Makati, Quezon City, Cebu, Taguig, Pasig, Davao
Cambodia | | Phnom Penh
Myanmar | burma | Yangon
Australia | ~au, ~aus | Sydney, Melbourne, Brisbane
New Zealand | aotearoa, ~nz | Auckland, Wellington, Christchurch
"""

# States and provinces, under their country. CA, CO, DE, HI, ID, IN, LA, ME, OK, OR, NL, TN and the like are too
# ambiguous ("Berlin, DE") to look for after a comma, so they only work for typing.
STATES = {
    "United States": """
Alabama | =al | Huntsville, Montgomery
Alaska | =ak | Anchorage
Arizona | =az | Phoenix, Tucson, Scottsdale, Tempe, Chandler, Mesa
Arkansas | =ar | Little Rock, Bentonville, Fayetteville
California | ~ca, ~calif | SF Bay Area, Southern California, Sacramento, Santa Barbara, Santa Cruz
Colorado | ~co | Denver, Boulder, Colorado Springs, Fort Collins
Connecticut | =ct | Hartford, New Haven, Stamford
Delaware | ~de | Wilmington
Florida | =fl | Miami, Tampa, Orlando, Jacksonville, Fort Lauderdale, Boca Raton, Tallahassee, Gainesville
Georgia | =ga | Atlanta, Savannah, Alpharetta
Hawaii | ~hi | Honolulu
Idaho | ~id | Boise
Illinois | =il | Chicago, Evanston, Naperville, Champaign, Urbana
Indiana | ~in | Indianapolis, Bloomington, West Lafayette
Iowa | =ia | Des Moines, Iowa City
Kansas | =ks | Wichita, Overland Park, Lawrence
Kentucky | =ky | Louisville, Lexington
Louisiana | ~la | New Orleans, Baton Rouge
Maine | ~me | Bangor
Maryland | =md | Baltimore, Bethesda, Rockville, Annapolis
Massachusetts | =ma, ~mass | Boston, Somerville, Waltham, Worcester
Michigan | =mi | Detroit, Ann Arbor, Grand Rapids, Lansing
Minnesota | =mn | Minneapolis, Saint Paul
Mississippi | =ms |
Missouri | =mo | St. Louis, Kansas City
Montana | =mt | Bozeman, Missoula
Nebraska | =ne | Omaha
Nevada | =nv | Las Vegas, Reno
New Hampshire | =nh | Nashua
New Jersey | =nj | Newark, Jersey City, Hoboken, Princeton
New Mexico | =nm | Albuquerque, Santa Fe
New York State | =ny, ~nys | New York City, Buffalo, Rochester, Ithaca, Albany
North Carolina | =nc | Research Triangle, Charlotte
North Dakota | =nd | Fargo
Ohio | =oh | Columbus, Cleveland, Cincinnati, Dayton
Oklahoma | ~ok | Oklahoma City, Tulsa
Oregon | ~or | Portland, Eugene, Beaverton, Bend
Pennsylvania | =pa | Philadelphia, Pittsburgh
Rhode Island | =ri | Providence
South Carolina | =sc | Charleston, Greenville
South Dakota | =sd | Sioux Falls
Tennessee | ~tn | Nashville, Memphis, Knoxville, Chattanooga
Texas | =tx | Austin, Dallas, Houston, San Antonio, Fort Worth, Plano, Frisco, El Paso
Utah | =ut | Salt Lake City, Provo, Lehi, Ogden
Vermont | =vt | Burlington
Virginia | =va | Arlington, Reston, Herndon, Tysons, McLean, Virginia Beach, Charlottesville
Washington State | =wa, ~washington | Seattle, Redmond, Bellevue, Kirkland, Spokane, Tacoma
West Virginia | =wv | Morgantown
Wisconsin | =wi | Madison, Milwaukee
Wyoming | =wy | Cheyenne
""",
    "Canada": """
Ontario | =on, ~ont | Greater Toronto Area, Ottawa, Waterloo, Kitchener, Guelph
British Columbia | =bc | Vancouver, Burnaby, Kelowna
Quebec | =qc | Montreal, Quebec City, Laval, Gatineau, Sherbrooke
Alberta | =ab | Calgary, Edmonton
Manitoba | =mb | Winnipeg
Saskatchewan | =sk | Saskatoon, Regina
Nova Scotia | =ns | Halifax
New Brunswick | =nb | Fredericton, Moncton
Newfoundland and Labrador | ~nl, newfoundland | St. John's
""",
    "India": """
Karnataka | | Bengaluru, Mysuru, Mangaluru
Maharashtra | | Mumbai, Pune, Nagpur, Navi Mumbai, Thane
Telangana | | Hyderabad, Secunderabad
Tamil Nadu | | Chennai, Coimbatore, Madurai
Uttar Pradesh | | Noida, Lucknow, Kanpur
Haryana | | Gurugram, Faridabad
Gujarat | | Ahmedabad, Gandhinagar, Surat, Vadodara
Kerala | | Kochi, Thiruvananthapuram, Kozhikode
West Bengal | | Kolkata
Rajasthan | | Jaipur
Andhra Pradesh | | Visakhapatnam, Vijayawada
Madhya Pradesh | | Indore, Bhopal
Punjab | | Chandigarh, Mohali, Ludhiana
Odisha | | Bhubaneswar
""",
    "Australia": """
New South Wales | nsw | Sydney
Victoria | vic | Melbourne, Geelong
Queensland | qld | Brisbane, Gold Coast
Western Australia | | Perth
South Australia | | Adelaide
Australian Capital Territory | act | Canberra
Tasmania | tas | Hobart
""",
}

# Metro areas, under the state or country that holds them.
METROS = {
    "California": """
SF Bay Area | sf, bay area, sfba, silicon valley, san francisco bay area | San Francisco, Bay Area, Oakland, \
Berkeley, Palo Alto, Mountain View, Sunnyvale, Menlo Park, Santa Clara, Cupertino, Redwood City, San Mateo, San Jose, \
Fremont, Emeryville, Los Gatos, South Bay
Southern California | socal | Los Angeles, San Diego, Irvine
""",
    "Southern California": """
Los Angeles | ~la, ~l.a. | Santa Monica, Pasadena, Culver City, Burbank, Long Beach
""",
    "New York State": """
New York City | nyc, new york, ~ny city | Brooklyn, Manhattan, Queens, Bronx, Jersey City, Hoboken
""",
    "United States": """
Washington DC | dc, district of columbia, dmv | Arlington, Bethesda, Reston, Tysons, McLean, Silver Spring
""",
    "North Carolina": """
Research Triangle | ~rtp, ~triangle | Raleigh, Durham, Chapel Hill, Cary
""",
    "Ontario": """
Greater Toronto Area | gta | Toronto, Mississauga, Markham, Oakville, Brampton, Vaughan
""",
    "India": """
Delhi NCR | ncr, ~delhi ncr | New Delhi, Delhi, Gurugram, Noida, Greater Noida, Faridabad, Ghaziabad
""",
}

# What to search GitHub for, where a place's own name isn't what people write in their profile.
SEARCH = {
    "SF Bay Area": ["San Francisco", "Bay Area"], "New York City": ["New York", "Brooklyn"],
    "Los Angeles": ["Los Angeles", "Santa Monica"], "Southern California": ["Los Angeles", "San Diego"],
    "Washington DC": ["Washington DC", "Arlington"], "Research Triangle": ["Raleigh", "Durham"],
    "Greater Toronto Area": ["Toronto", "Mississauga"], "Delhi NCR": ["Delhi", "Gurugram"],
    "Washington State": ["Seattle", "Redmond", "Bellevue"], "New York State": ["New York", "Buffalo"],
}

# Other names for cities: "Bengaluru = Bangalore" means profiles saying either one are in Bengaluru.
CITY_NAMES = """
Bengaluru = Bangalore
Mumbai = Bombay
Chennai = Madras
Kolkata = Calcutta
Gurugram = Gurgaon
Kochi = Cochin
Thiruvananthapuram = Trivandrum
Mysuru = Mysore
Mangaluru = Mangalore
Visakhapatnam = Vizag
Munich = München
Cologne = Köln
Nuremberg = Nürnberg
Hanover = Hannover
Geneva = Genève, Genf
Bern = Berne
Vienna = Wien
Prague = Praha
Warsaw = Warszawa
Kraków = Cracow
Copenhagen = København
Gothenburg = Göteborg
Lisbon = Lisboa
Seville = Sevilla
Rome = Roma
Milan = Milano
Turin = Torino
Florence = Firenze
Naples = Napoli
Padua = Padova
Genoa = Genova
Venice = Venezia
Athens = Athina
Bucharest = București
Belgrade = Beograd
Brussels = Bruxelles, Brussel
Antwerp = Antwerpen
Ghent = Gent
The Hague = Den Haag
Kyiv = Kiev
Kharkiv = Kharkov
Odesa = Odessa
Moscow = Moskva
Saint Petersburg = St Petersburg, SPb
Beijing = Peking
Ho Chi Minh City = Saigon, HCMC, Ho Chi Minh
Hanoi = Ha Noi
Yangon = Rangoon
Mexico City = CDMX, Ciudad de México
Tel Aviv = TLV, Tel Aviv-Yafo
Quebec City = Québec
Saint Paul = St Paul
St. Louis = Saint Louis
Chattogram = Chittagong
Cluj-Napoca = Cluj
Marrakech = Marrakesh
Tangier = Tanger
Algiers = Alger
Frankfurt = Frankfurt am Main
Zurich = Zürich
Manila = Metro Manila
"""

# Typed on its own, these mean "no location filter" rather than a place.
ANYWHERE = {"anywhere", "worldwide", "global", "any", "everywhere", "world"}
MAX_SEARCH_TERMS = 4  # GitHub user searches per language; each place gets its first term before any gets a second


# -- building the lookup -------------------------------------------------------

FOLD = str.maketrans({"ł": "l", "ø": "o", "đ": "d", "ı": "i", "æ": "ae", "œ": "oe", "þ": "th", "ð": "d",
                      "-": " ", "–": " ", "_": " ", "(": " ", ")": " ", ".": "", "'": "", "’": ""})


def norm(text):
    """Lowercase, accents and dots dropped, dashes as spaces: "São Paulo" -> "sao paulo", "U.S." -> "us"."""
    text = unicodedata.normalize("NFKD", (text or "").casefold().translate(FOLD))
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


class Place:
    def __init__(self, name, kind):
        self.name, self.kind = name, kind
        self.typed, self.matched, self.inside = [], [], []


PLACES = {}  # norm(name) -> Place
LOOKUP = {}  # every typed name -> Place


def _register(name, kind, aliases=(), inside=()):
    place = PLACES.get(norm(name))
    if place is None:
        place = PLACES[norm(name)] = Place(name, kind)
        if "(" not in name:  # "Georgia (country)" is only typed; profiles say "Tbilisi"
            place.matched.append(norm(name))
        LOOKUP.setdefault(norm(name), place)
    for alias in aliases:
        marker, word = (alias[0], alias[1:]) if alias[:1] in "~=" else ("", alias)
        LOOKUP.setdefault(norm(word), place)
        if marker == "=":
            place.matched.append(", " + norm(word))
        elif not marker:
            place.matched.append(norm(word))
    place.inside += [n for n in inside if norm(n) != norm(name)]
    return place


def _lines(block):
    for line in block.replace("\\\n", "").strip().splitlines():
        name, _, rest = line.partition("|")
        aliases, _, inside = rest.partition("|")
        yield (name.strip(), [a.strip() for a in aliases.split(",") if a.strip()],
               [n.strip() for n in inside.split(",") if n.strip()])


def _build():
    for kind, block in (("region", REGIONS), ("country", COUNTRIES)):
        for name, aliases, inside in _lines(block):
            _register(name, kind, aliases, inside)
    for parents, kind in ((STATES, "state"), (METROS, "metro")):
        for parent, block in parents.items():
            for name, aliases, inside in _lines(block):
                _register(name, kind, aliases, inside)
                PLACES[norm(parent)].inside.append(name)
    for line in CITY_NAMES.strip().splitlines():
        name, _, others = line.partition("=")
        _register(name.strip(), "city", [o.strip() for o in others.split(",")])
    for place in list(PLACES.values()):
        for name in place.inside:
            if norm(name) not in LOOKUP:
                _register(name, "city")
    _register("Remote", "remote", ["~remote only", "~fully remote"])


_build()


@lru_cache(maxsize=None)
def _match_terms(key, _seen=frozenset()):
    place = PLACES[key]
    terms = list(place.matched)
    for name in place.inside:
        inner = LOOKUP[norm(name)]
        inner_key = norm(inner.name)
        if inner_key not in _seen and inner_key != key:
            terms += _match_terms(inner_key, _seen | {key})
    return tuple(dict.fromkeys(terms))


def match_terms(place):
    return list(_match_terms(norm(place.name)))


def cities(place, limit=None, _seen=None):
    """City names inside a place, biggest first as listed, for showing what a region or country covers."""
    _seen = _seen if _seen is not None else set()
    out = []
    for name in place.inside:
        inner = LOOKUP[norm(name)]
        if inner.name in _seen:
            continue
        _seen.add(inner.name)
        out += [inner.name] if inner.kind == "city" else cities(inner, None, _seen)
    return out[:limit] if limit else out


def search_terms(place):
    """What to put in GitHub's location: search for this place, most useful first."""
    if place.name in SEARCH:
        return SEARCH[place.name]
    if place.kind == "region":
        return [search_terms(LOOKUP[norm(n)])[0] for n in place.inside[:3]]
    if place.kind == "metro":
        return [place.inside[0], place.inside[1]] if len(place.inside) > 1 else [place.name]
    if place.kind == "state":
        return [n for n in cities(place, 2)] + [place.name]
    if place.kind == "country":
        firsts = [search_terms(LOOKUP[norm(n)])[0] if LOOKUP[norm(n)].kind != "city" else n for n in place.inside[:2]]
        return [place.name] + firsts
    alternates = [m for m in place.matched if m != norm(place.name) and not m.startswith(",")]
    return [place.name] + alternates[:1]


# -- resolving what someone typed ---------------------------------------------------

def _qualifies(part, container):
    """True when `container` only says where `part` is, as Texas does in "Austin, TX" and the US in "Texas, USA"."""
    if container is None or container.kind not in ("state", "country") or part is container:
        return False
    if part is None or part.kind == "city":
        return True  # a town followed by a state or country is that town, wherever else the name is used
    return part.kind in ("metro", "state") and norm(part.name) in _match_terms(norm(container.name))


def resolve(raw):
    """What a location box says: {"places": [...], "match": [...], "search": [...], "notes": [...]}.

    Parts are separated by commas and any of them can match. A state or country right after a place only says where
    that place is ("Austin, TX", "Portland, Oregon", "Waterloo, ON, Canada"), so it doesn't widen the search.
    """
    parts = [" ".join(p.split()) for p in (raw or "").split(",") if p.strip()]
    places, notes = [], []
    i = 0
    while i < len(parts):
        text = parts[i]
        key = norm(text)
        place = LOOKUP.get(key)
        label, inner = text, place
        i += 1
        while i < len(parts) and key not in ANYWHERE and _qualifies(inner, LOOKUP.get(norm(parts[i]))):
            label, inner = f"{label}, {parts[i]}", LOOKUP.get(norm(parts[i]))
            i += 1
        if key in ANYWHERE:
            notes.append(f"“{text}” means no location filter, so it was left out.")
            continue
        if place is None and len(key) <= 2:
            notes.append(f"“{text}” is too short to match on its own, so it was left out.")
            continue
        if place is None:
            places.append({"input": label, "name": text, "kind": "unknown", "known": False, "match": [key],
                           "search": [text], "covers": [], "count": 1})
            notes.append(f"“{text}” isn't in the built-in places, so it keeps profiles whose location says “{text}”.")
            continue
        covers = cities(place)
        if place.kind == "region":  # one city from each country in turn, so LATAM shows more than Brazil
            members = [cities(LOOKUP[norm(n)]) or [LOOKUP[norm(n)].name] for n in place.inside]
            covers = list(dict.fromkeys(c for row in zip_longest(*members) for c in row if c))
        places.append({"input": label, "name": place.name, "kind": place.kind, "known": True,
                       "match": match_terms(place), "search": search_terms(place),
                       "covers": covers[:8], "count": len(covers)})

    match = list(dict.fromkeys(t for p in places for t in p["match"]))
    search = []
    for depth in range(3):
        for p in places:
            if depth < len(p["search"]) and len(search) < MAX_SEARCH_TERMS:
                term = norm(p["search"][depth]) if p["known"] else p["search"][depth].lower()
                if term not in search:
                    search.append(term)
    return {"places": places, "match": match, "search": search, "notes": notes}


@lru_cache(maxsize=64)
def _pattern(terms):
    alternatives = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    # Letter boundaries so "uk" doesn't match "ukraine" and "sf" doesn't match "sfax".
    return re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])")


def matches(location, terms):
    if not terms:
        return True
    return bool(_pattern(tuple(terms)).search(norm(location)))


def describe(resolved):
    """One line for the terminal: what each typed place covers."""
    out = []
    for p in resolved["places"]:
        if not p["known"]:
            out.append(f"{p['input']} (as written)")
        elif p["kind"] == "city":
            out.append(p["input"] if norm(p["input"]).startswith(norm(p["name"])) else f"{p['name']} ({p['input']})")
        elif p["count"]:
            more = f" +{p['count'] - len(p['covers'])} more" if p["count"] > len(p["covers"]) else ""
            out.append(f"{p['name']} ({', '.join(p['covers'][:5])}{more if p['count'] > 5 else ''})")
        else:
            out.append(p["name"])
    return "; ".join(out)


# Names a job description might use for a place, longest first, for finding the role's location in a JD. Common
# words and first names that are also places ("Phoenix" the framework, "Split", "Jordan") aren't looked for.
JD_SKIP = {"phoenix", "split", "nice", "bend", "victoria", "lincoln", "georgia", "jordan", "sofia", "florence", "laval",
           "regina", "cary", "providence", "lawrence", "bloomington", "eugene", "madison", "durham", "reading", "bath",
           "santiago", "remote", "bay area", "south bay", "lima", "mobile", "concord", "triangle", "act", "eu"}


@lru_cache(maxsize=1)
def _jd_names():
    names = {}
    for key, place in LOOKUP.items():
        if len(key) >= 4 and key not in JD_SKIP and place.kind != "remote":
            names[key] = place
    return sorted(names, key=len, reverse=True)


def find_in_text(text):
    """Places named in a piece of text (a JD's location line), as names the location box understands."""
    low = norm(text)
    found = {}
    for key in _jd_names():
        pattern = rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])"
        match = re.search(pattern, low)
        if match:
            found.setdefault(LOOKUP[key].name.lower(), match.start())
            low = re.sub(pattern, lambda m: " " * len(m.group()), low)  # keep positions for the ordering
    return sorted(found, key=found.get)  # in the order the text names them, so "Lisbon, Portugal" stays one place
