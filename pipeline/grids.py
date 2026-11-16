"""Which synchronous grid (interconnection) and which operator group each region belongs to.

North America runs as a few separate AC grids that are tied together only by DC links and
back-to-back converters: Eastern, Western, Texas (ERCOT) and Québec, plus Alaska, Hawaii
and Newfoundland. Inside one grid every plant swings in lock-step; between grids power
moves only as fast as someone schedules it. The map shows the grids as groups, and the
operators inside each grid as sub-groups.

Codes are the region codes of regions.geojson (EIA-930 balancing authorities, plus
Canadian provinces). A region missing here is listed as "Other" by regions.py.
"""

GRIDS = {
    "east": "Eastern Interconnection",
    "west": "Western Interconnection",
    "texas": "Texas (ERCOT)",
    "quebec": "Québec",
    "alaska": "Alaska",
    "hawaii": "Hawaii",
    "newfoundland": "Newfoundland",
}

# Operator groups: key -> (grid, label, member region codes).
OPERATORS = {
    # Eastern
    "pjm": ("east", "PJM", ["PJM", "OVEC"]),
    "miso": ("east", "MISO", ["MISO", "EEI"]),
    "spp": ("east", "SPP", ["SWPP", "SPA"]),
    "nyiso": ("east", "NYISO", ["NYIS"]),
    "isone": ("east", "ISO-NE", ["ISNE"]),
    "southeast": ("east", "Southeast utilities", [
        "SOCO", "TVA", "DUK", "CPLE", "CPLW", "SCEG", "SC", "SEPA", "AEC", "LGEE", "YAD", "AECI"]),
    "florida": ("east", "Florida", ["FPL", "FPC", "TEC", "JEA", "TAL", "GVL", "FMPP", "SEC", "NSB", "HST"]),
    "ontario": ("east", "Ontario (IESO)", ["IESO"]),
    "prairies": ("east", "Manitoba and Saskatchewan", ["MHEB", "SPC"]),
    "maritimes": ("east", "Maritimes", ["NBSO", "NSPI"]),
    # Western
    "caiso": ("west", "California", ["CISO", "BANC", "LDWP", "IID", "TIDC"]),
    "northwest": ("west", "Pacific Northwest", [
        "BPAT", "PGE", "PSEI", "SCL", "TPWR", "AVA", "GCPD", "CHPD", "DOPD", "PACW", "IPCO", "AVRN",
        "NWMT", "GWA", "WWA"]),
    "southwest": ("west", "Southwest and Rockies", [
        "AZPS", "SRP", "TEPC", "PNM", "EPE", "NEVP", "WALC", "DEAA", "HGMA", "PSCO", "PACE",
        "GRID", "GRIS", "GRIF", "GRMA"]),
    "sppwest": ("west", "SPP West", ["SWPW"]),
    "canwest": ("west", "Alberta and B.C.", ["AESO", "BCHA"]),
    # The rest
    "ercot": ("texas", "ERCOT", ["ERCO"]),
    "hq": ("quebec", "Hydro-Québec", ["HQT"]),
    "nl": ("newfoundland", "Newfoundland and Labrador Hydro", ["NLH"]),
    "chugach": ("alaska", "Chugach (Railbelt)", ["CEA"]),
    "heco": ("hawaii", "Hawaiian Electric", ["HECO"]),
}

OPERATOR_OF = {code: key for key, (_, _, codes) in OPERATORS.items() for code in codes}
GRID_OF = {code: OPERATORS[key][0] for code, key in OPERATOR_OF.items()}


def grid_of(code):
    return GRID_OF.get(code, "other")


def operator_of(code):
    return OPERATOR_OF.get(code, "other")


def catalog():
    """Group list for the page: grids, each with its operators (in order)."""
    return [{"key": g, "name": name,
             "operators": [{"key": k, "name": label, "regions": codes}
                           for k, (grid, label, codes) in OPERATORS.items() if grid == g]}
            for g, name in GRIDS.items()]
