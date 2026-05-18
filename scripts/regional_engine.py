import json
import os
from datetime import datetime

try:
    import requests
except Exception:
    requests = None

# ============================================
# TON GOOGLE SHEET = SOCLE NATIONAL FIABLE
# ============================================
# C'est LA base de données maître de BEAVER.WATCH.
# Données dures, stables, que TU contrôles, qui ne
# bloquent JAMAIS (contrairement à Google Trends).
# castorScore = score national de référence.
SHEET_URL = "https://script.google.com/macros/s/AKfycby3j6ytF7bYnz2icrhxVng5QawSrzUMvaRccj75xwfNqdWqqcO52wQwwZRv4mggiopjqg/exec"

_SHEET_CACHE = {"data": None, "fetched": False}


def get_sheet():
    """Récupère TON Google Sheet une seule fois (mise en cache).
    Source dure : si indisponible, le moteur continue sans planter."""
    if _SHEET_CACHE["fetched"]:
        return _SHEET_CACHE["data"]
    _SHEET_CACHE["fetched"] = True
    if requests is None:
        return None
    try:
        r = requests.get(SHEET_URL, timeout=15)
        if r.status_code == 200:
            _SHEET_CACHE["data"] = r.json()
            print("✅ Google Sheet (socle) chargé")
        else:
            print(f"⚠️ Sheet HTTP {r.status_code} — moteur continue sans socle")
    except Exception as e:
        print(f"⚠️ Sheet indisponible ({str(e)[:60]}) — moteur continue sans socle")
    return _SHEET_CACHE["data"]

# ============================================
# BEAVER.WATCH — Moteur de Score Régional
# ============================================
#
# CONÇU POUR GRANDIR (voir PLAN-INDICE-REGIONAL.md)
#
# Le moteur ne connaît PAS un nombre fixe de sources.
# Aujourd'hui : 3 signaux (chômage, vol auto, reventes).
# Demain : faillites, gig/Uber, immobilier, criminalité...
#
# Pour ajouter une source : écrire son loader dans
# SOURCE_LOADERS. Le reste suit automatiquement.
#
# RÈGLES (du plan) :
# - Jamais de faux chiffre : si peu de signaux, on le dit
# - On expose le NOMBRE de signaux, jamais lesquels
# - Score = moyenne pondérée des signaux DISPONIBLES
# ============================================

# Province (1re lettre code postal) -> clés dans les JSON
PROVINCE_BY_LETTER = {
    "A": "NL", "B": "NS", "C": "PE", "E": "NB",
    "G": "QC", "H": "QC", "J": "QC",
    "K": "ON", "L": "ON", "M": "ON", "N": "ON", "P": "ON",
    "R": "MB", "S": "SK", "T": "AB", "V": "BC",
    "X": "NT", "Y": "YT",
}

# Ville (préfixe FSA 2 lettres) -> clé ville dans kijiji_data.json
CITY_BY_FSA2 = {
    # Montréal
    "H1": "montreal", "H2": "montreal", "H3": "montreal", "H4": "montreal",
    "H5": "montreal", "H8": "montreal", "H9": "montreal", "H7": "montreal",
    # Toronto (RGT)
    "M1": "toronto", "M2": "toronto", "M3": "toronto", "M4": "toronto",
    "M5": "toronto", "M6": "toronto", "M8": "toronto", "M9": "toronto",
    "L3": "toronto", "L4": "toronto", "L5": "toronto", "L6": "toronto",
    # Vancouver (Grand Vancouver)
    "V5": "vancouver", "V6": "vancouver", "V7": "vancouver",
    "V3": "vancouver", "V4": "vancouver",
}

# Province -> clé région dans auto_theft_data.json
AUTOTHEFT_REGION_BY_PROV = {
    "ON": "ontario", "QC": "quebec", "BC": "bc", "AB": "alberta",
}


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ============================================
# LOADERS DE SIGNAUX
# Chaque loader retourne un stress 0-1 pour la zone, ou None.
# Pour AJOUTER UNE SOURCE : ajouter une fonction ici
# puis l'enregistrer dans SOURCE_LOADERS plus bas.
# ============================================

def signal_socle_national(prov, city, fsa2):
    """SOCLE : score national de TON Google Sheet (castorScore).
    Source dure et stable. Présent pour TOUTES les zones tant
    que le Sheet répond — garantit qu'un score s'affiche
    toujours, même si tous les autres signaux tombent."""
    s = get_sheet()
    if not s:
        return None
    v = s.get("castorScore")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if 0.0 <= v <= 1.0:
        return v
    return None


def signal_chomage(prov, city, fsa2):
    d = load_json("chomage_data.json")
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
    # fallback national
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
        sc = cd.get("composite_score")
        if sc is not None:
            return float(sc)
    # fallback national
    if d.get("national_score") is not None:
        return float(d["national_score"])
    return None


def signal_detresse(prov, city, fsa2):
    """Recherches de détresse financière (Google Trends régional).
    Signal prédictif : précède les faillites officielles."""
    d = load_json("detresse_data.json")
    if not d:
        return None
    p = d.get("provinces", {}).get(prov)
    if p and p.get("stress_score") is not None:
        return float(p["stress_score"])
    # fallback national
    if d.get("national_score") is not None:
        return float(d["national_score"])
    return None


# ============================================
# REGISTRE DES SOURCES — c'est ICI qu'on grandit
# poids initial fixe ; l'IA les ajustera plus tard
# (étape 11 du plan : apprentissage via Track Record)
# ============================================
SOURCE_LOADERS = [
    {"id": "socle_national", "loader": signal_socle_national, "poids": 0.34, "geo": "national"},
    {"id": "chomage",   "loader": signal_chomage,  "poids": 0.24, "geo": "province"},
    {"id": "vol_auto",  "loader": signal_vol_auto, "poids": 0.16, "geo": "province"},
    {"id": "reventes",  "loader": signal_reventes, "poids": 0.14, "geo": "ville"},
    {"id": "detresse",  "loader": signal_detresse, "poids": 0.12, "geo": "province"},
    # ÉTAPES FUTURES (plan) — décommenter quand le scraper existe :
    # {"id": "gig_uber",   "loader": signal_gig,        "poids": ?, "geo": "ville"},
    # {"id": "immobilier", "loader": signal_immobilier, "poids": ?, "geo": "province"},
]

# Nombre total de signaux PRÉVUS (pour le niveau de confiance honnête)
SIGNAUX_PREVUS_TOTAL = 12


def compute_zone(prov, city, fsa2):
    """Calcule le score d'une zone à partir des signaux disponibles."""
    contributions = []  # (stress, poids)
    n_actifs = 0

    for src in SOURCE_LOADERS:
        try:
            val = src["loader"](prov, city, fsa2)
        except Exception:
            val = None
        if val is not None and 0.0 <= val <= 1.0:
            contributions.append((val, src["poids"]))
            n_actifs += 1

    if not contributions:
        return None

    total_w = sum(w for _, w in contributions)
    score = sum(s * w for s, w in contributions) / total_w
    score = round(score, 3)

    if score >= 0.65:
        niveau = {"fr": "CRISE", "en": "CRISIS", "emoji": "🔴"}
    elif score >= 0.45:
        niveau = {"fr": "TENSION", "en": "TENSION", "emoji": "🟠"}
    elif score >= 0.30:
        niveau = {"fr": "À SURVEILLER", "en": "WATCH", "emoji": "🟡"}
    else:
        niveau = {"fr": "STABLE", "en": "STABLE", "emoji": "🟢"}

    # Niveau de confiance honnête (du plan : jamais de fausse précision)
    if n_actifs >= 3:
        confiance = {"fr": "Bonne", "en": "Good"}
    elif n_actifs == 2:
        confiance = {"fr": "Modérée", "en": "Moderate"}
    else:
        confiance = {"fr": "Préliminaire", "en": "Preliminary"}

    return {
        "score": score,
        "score_100": round(score * 100),
        "niveau": niveau,
        "signaux_actifs": n_actifs,
        "signaux_prevus": SIGNAUX_PREVUS_TOTAL,
        "confiance": confiance,
    }


def run():
    print("🦫 BEAVER.WATCH — Moteur de Score Régional")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"🔧 {len(SOURCE_LOADERS)} signaux actifs / {SIGNAUX_PREVUS_TOTAL} prévus")
    print("=" * 52)

    # On calcule un score par province + variantes ville connues.
    output = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "note": "Score régional BEAVER. Conçu pour grandir (voir plan). "
                "On expose le nombre de signaux, jamais lesquels.",
        "signaux_actifs_max": len(SOURCE_LOADERS),
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
            print(f"  ⚠️ {prov}: aucun signal disponible")

    print("\n🏙️ Score par ville (signal reventes dispo)")
    for city in ("montreal", "toronto", "vancouver"):
        # province de référence pour la ville
        prov = {"montreal": "QC", "toronto": "ON", "vancouver": "BC"}[city]
        res = compute_zone(prov, city, None)
        if res:
            output["villes"][city] = res
            print(f"  {res['niveau']['emoji']} {city}: {res['score_100']}/100 "
                  f"· {res['signaux_actifs']} signaux · {res['confiance']['fr']}")

    with open("regional_scores.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    np = len(output["provinces"])
    nv = len(output["villes"])
    print(f"\n✅ regional_scores.json sauvegardé — {np} provinces · {nv} villes")
    if np == 0 and nv == 0:
        print("⚠️ Aucun score (sources JSON absentes ?)")
    else:
        print("\n🦫 Done!")


if __name__ == "__main__":
    run()
