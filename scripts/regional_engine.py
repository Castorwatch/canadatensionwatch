import json
from datetime import datetime

try:
    import requests
except Exception:
    requests = None

# BEAVER.WATCH — Moteur de Score Régional
# Socle = ton Google Sheet (castorScore) + signaux scrapés
# (chômage, immobilier, vol auto, reventes, détresse).
# Robuste : si une source tombe, le moteur continue.

SHEET_URL = "https://script.google.com/macros/s/AKfycby3j6ytF7bYnz2icrhxVng5QawSrzUMvaRccj75xwfNqdWqqcO52wQwwZRv4mggiopjqg/exec"
_SHEET = {"data": None, "done": False}


def get_sheet():
    if _SHEET["done"]:
        return _SHEET["data"]
    _SHEET["done"] = True
    if requests is None:
        print("⚠️ requests absent — sans socle")
        return None
    try:
        r = requests.get(SHEET_URL, timeout=15)
        if r.status_code == 200:
            _SHEET["data"] = r.json()
            print("✅ Google Sheet (socle) chargé")
        else:
            print(f"⚠️ Sheet HTTP {r.status_code} — sans socle")
    except Exception as e:
        print(f"⚠️ Sheet indisponible ({str(e)[:50]}) — sans socle")
    return _SHEET["data"]


PROVINCE_BY_LETTER = {
    "A": "NL", "B": "NS", "C": "PE", "E": "NB",
    "G": "QC", "H": "QC", "J": "QC",
    "K": "ON", "L": "ON", "M": "ON", "N": "ON", "P": "ON",
    "R": "MB", "S": "SK", "T": "AB", "V": "BC",
    "X": "NT", "Y": "YT",
}

CITY_BY_FSA2 = {
    "H1": "montreal", "H2": "montreal", "H3": "montreal", "H4": "montreal",
    "H5": "montreal", "H7": "montreal", "H8": "montreal", "H9": "montreal",
    "M1": "toronto", "M2": "toronto", "M3": "toronto", "M4": "toronto",
    "M5": "toronto", "M6": "toronto", "M8": "toronto", "M9": "toronto",
    "L3": "toronto", "L4": "toronto", "L5": "toronto", "L6": "toronto",
    "V3": "vancouver", "V4": "vancouver", "V5": "vancouver",
    "V6": "vancouver", "V7": "vancouver",
}

AUTOTHEFT_REGION_BY_PROV = {
    "ON": "ontario", "QC": "quebec", "BC": "bc", "AB": "alberta",
}


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def signal_socle_national(prov, city, fsa2):
    s = get_sheet()
    if not s:
        return None
    try:
        v = float(s.get("castorScore"))
    except (TypeError, ValueError):
        return None
    return v if 0.0 <= v <= 1.0 else None


def signal_chomage(prov, city, fsa2):
    d = load_json("chomage_data.json")
    if not d:
        return None
    p = d.get("provinces", {}).get(prov)
    if p and p.get("stress_score") is not None:
        return float(p["stress_score"])
    return None


def signal_immobilier(prov, city, fsa2):
    d = load_json("immobilier_data.json")
    if not d:
        return None
    p = d.get("provinces", {}).get(prov)
    if p and p.get("stress_score") is not None:
        return float(p["stress_score"])
    return None


def signal_vol_auto(prov, city, fsa2):
    d = load_json("auto_theft_data.json")
    if not d:
        return None
    regions = d.get("regions", {})
    rk = AUTOTHEFT_REGION_BY_PROV.get(prov)
    if rk and regions.get(rk, {}).get("composite_score") is not None:
        return float(regions[rk]["composite_score"])
    if regions.get("canada", {}).get("composite_score") is not None:
        return float(regions["canada"]["composite_score"])
    if d.get("national_score") is not None:
        return float(d["national_score"])
    return None


def signal_reventes(prov, city, fsa2):
    d = load_json("kijiji_data.json")
    if not d:
        return None
    if city:
        cd = d.get("cities", {}).get(city, {})
        if cd.get("composite_score") is not None:
            return float(cd["composite_score"])
    if d.get("national_score") is not None:
        return float(d["national_score"])
    return None


def signal_detresse(prov, city, fsa2):
    d = load_json("detresse_data.json")
    if not d:
        return None
    p = d.get("provinces", {}).get(prov)
    if p and p.get("stress_score") is not None:
        return float(p["stress_score"])
    if d.get("national_score") is not None:
        return float(d["national_score"])
    return None


SOURCE_LOADERS = [
    {"id": "socle_national", "loader": signal_socle_national, "poids": 0.32},
    {"id": "chomage",        "loader": signal_chomage,        "poids": 0.22},
    {"id": "immobilier",     "loader": signal_immobilier,     "poids": 0.16},
    {"id": "vol_auto",       "loader": signal_vol_auto,       "poids": 0.14},
    {"id": "reventes",       "loader": signal_reventes,       "poids": 0.10},
    {"id": "detresse",       "loader": signal_detresse,       "poids": 0.06},
]

SIGNAUX_PREVUS_TOTAL = 12


def compute_zone(prov, city, fsa2):
    contributions = []
    n = 0
    for src in SOURCE_LOADERS:
        try:
            v = src["loader"](prov, city, fsa2)
        except Exception:
            v = None
        if v is not None and 0.0 <= v <= 1.0:
            contributions.append((v, src["poids"]))
            n += 1
    if not contributions:
        return None

    total_w = sum(w for _, w in contributions)
    score = round(sum(s * w for s, w in contributions) / total_w, 3)

    if score >= 0.65:
        niveau = {"fr": "CRISE", "en": "CRISIS", "emoji": "🔴"}
    elif score >= 0.45:
        niveau = {"fr": "TENSION", "en": "TENSION", "emoji": "🟠"}
    elif score >= 0.30:
        niveau = {"fr": "À SURVEILLER", "en": "WATCH", "emoji": "🟡"}
    else:
        niveau = {"fr": "STABLE", "en": "STABLE", "emoji": "🟢"}

    if n >= 4:
        confiance = {"fr": "Bonne", "en": "Good"}
    elif n >= 2:
        confiance = {"fr": "Modérée", "en": "Moderate"}
    else:
        confiance = {"fr": "Préliminaire", "en": "Preliminary"}

    return {
        "score": score,
        "score_100": round(score * 100),
        "niveau": niveau,
        "signaux_actifs": n,
        "signaux_prevus": SIGNAUX_PREVUS_TOTAL,
        "confiance": confiance,
    }


def run():
    print("🦫 BEAVER.WATCH — Moteur de Score Régional")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"🔧 {len(SOURCE_LOADERS)} signaux configurés / {SIGNAUX_PREVUS_TOTAL} prévus")
    print("=" * 52)

    get_sheet()

    output = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "note": "Socle = Google Sheet (castorScore) + signaux officiels. "
                "On expose le nombre de signaux, jamais lesquels.",
        "signaux_configures": len(SOURCE_LOADERS),
        "signaux_prevus_total": SIGNAUX_PREVUS_TOTAL,
        "provinces": {},
        "villes": {},
    }

    provinces = sorted(set(PROVINCE_BY_LETTER.values()))
    print("\n📊 Score par province")
    for prov in provinces:
        res = compute_zone(prov, None, None)
        if res:
            output["provinces"][prov] = res
            print(f"  {res['niveau']['emoji']} {prov}: {res['score_100']}/100 "
                  f"· {res['signaux_actifs']} signaux · {res['confiance']['fr']}")
        else:
            print(f"  ⚠️ {prov}: aucun signal")

    print("\n🏙️ Score par ville")
    for city in ("montreal", "toronto", "vancouver"):
        prov = {"montreal": "QC", "toronto": "ON", "vancouver": "BC"}[city]
        res = compute_zone(prov, city, None)
        if res:
            output["villes"][city] = res
            print(f"  {res['niveau']['emoji']} {city}: {res['score_100']}/100 "
                  f"· {res['signaux_actifs']} signaux · {res['confiance']['fr']}")

    with open("regional_scores.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    npv = len(output["provinces"])
    nv = len(output["villes"])
    print(f"\n✅ regional_scores.json — {npv} provinces · {nv} villes")
    print("\n🦫 Done!" if (npv or nv) else "⚠️ Aucun score")


if __name__ == "__main__":
    run()
