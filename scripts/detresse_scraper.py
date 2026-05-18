import json
import time
import random
from datetime import datetime

# ============================================
# BEAVER.WATCH — Scraper Signaux de Détresse
# Source : Google Trends régional (pytrends)
# ============================================
#
# Capte les recherches qui PRÉCÈDENT la détresse
# financière officielle de plusieurs mois :
# "faillite", "huissier", "expulsion", "aide juridique".
#
# Quand les gens cherchent ça, ils sont DÉJÀ en
# difficulté — bien avant que le Bureau du Surintendant
# des Faillites ne publie les chiffres (5-6 semaines
# de retard + comptabilise seulement les faillites
# DÉPOSÉES, pas celles qui se préparent).
#
# = signal prédictif, pas rétroviseur.
#
# Même technique que auto_theft_scraper.py (éprouvée).
# Sortie : detresse_data.json (lu par regional_engine.py)
# ============================================

# Termes de détresse — bilingues (le Canada cherche en FR au QC,
# en EN dans le reste). On envoie les termes adaptés à chaque région.
TERMES_FR = [
    "faillite personnelle",
    "syndic faillite",
    "huissier",
    "saisie salaire",
]
TERMES_EN = [
    "personal bankruptcy",
    "bankruptcy trustee",
    "wage garnishment",
    "debt help",
]

# Régions canadiennes (codes Google Trends) + langue dominante
REGIONS = {
    "canada":  {"geo": "CA",    "prov": None, "name_fr": "Canada",                "lang": "en"},
    "ontario": {"geo": "CA-ON", "prov": "ON", "name_fr": "Ontario",               "lang": "en"},
    "quebec":  {"geo": "CA-QC", "prov": "QC", "name_fr": "Québec",                "lang": "fr"},
    "bc":      {"geo": "CA-BC", "prov": "BC", "name_fr": "Colombie-Britannique",  "lang": "en"},
    "alberta": {"geo": "CA-AB", "prov": "AB", "name_fr": "Alberta",               "lang": "en"},
    "manitoba":{"geo": "CA-MB", "prov": "MB", "name_fr": "Manitoba",              "lang": "en"},
}


def get_trend_score(terms, geo, lang):
    """Récupère l'intérêt moyen Google Trends (7 derniers jours)."""
    try:
        from pytrends.request import TrendReq
        hl = "fr-CA" if lang == "fr" else "en-CA"
        pytrends = TrendReq(hl=hl, tz=-300, timeout=(10, 25), retries=2)
        time.sleep(random.uniform(10, 15))  # délai anti-blocage (comme auto_theft)
        pytrends.build_payload(terms[:5], cat=0, timeframe="now 7-d", geo=geo, gprop="")
        data = pytrends.interest_over_time()
        if data.empty:
            return None
        scores = [data[t].mean() for t in terms if t in data.columns]
        return round(sum(scores) / len(scores), 1) if scores else None
    except Exception as e:
        print(f"    Trends error: {e}")
        return None


def normalize_score(raw, baseline=20):
    """
    Convertit l'intérêt Trends en stress 0-1.
    baseline=20 : niveau "normal" de recherches de détresse.
    Au-dessus = détresse anormale qui monte.
    """
    if raw is None:
        return None
    if raw <= baseline:
        return round(max(0.05, (raw / baseline) * 0.35), 2)
    excess = raw - baseline
    return round(min(1.0, 0.35 + (excess / (100 - baseline)) * 0.65), 2)


def status_from_score(s):
    if s is None:
        return {"fr": "N/A", "en": "N/A", "emoji": "❓"}
    if s < 0.35:
        return {"fr": "Normal", "en": "Normal", "emoji": "🟢"}
    if s < 0.55:
        return {"fr": "Surveiller", "en": "Watch", "emoji": "🟡"}
    if s < 0.70:
        return {"fr": "Tension", "en": "Tension", "emoji": "🟠"}
    return {"fr": "Critique", "en": "Critical", "emoji": "🔴"}


def run():
    print("🦫 BEAVER.WATCH — Scraper Signaux de Détresse")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("🔍 Source : Google Trends régional (recherches de détresse)")
    print("=" * 52)

    output = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "Google Trends — recherches de détresse financière régionales (bilingue FR/EN)",
        "termes_fr": TERMES_FR,
        "termes_en": TERMES_EN,
        "note": "Signal prédictif : précède les faillites officielles de plusieurs mois. Termes adaptés à la langue de chaque région.",
        "regions": {},
        "provinces": {},
        "national_score": None,
    }

    print("\n📊 Recherches de détresse par région (langue adaptée)")
    for key, info in REGIONS.items():
        terms = TERMES_FR if info["lang"] == "fr" else TERMES_EN
        print(f"\n  🔍 {info['name_fr']} [{info['lang'].upper()}]...", end=" ", flush=True)
        raw = get_trend_score(terms, info["geo"], info["lang"])
        score = normalize_score(raw)
        st = status_from_score(score)

        if raw is not None and score is not None:
            print(f"intérêt {raw} → stress {score} {st['emoji']}")
            entry = {
                "name_fr": info["name_fr"],
                "lang": info["lang"],
                "raw_interest": raw,
                "stress_score": score,
                "status": st,
            }
            output["regions"][key] = entry
            if key == "canada":
                output["national_score"] = score
            elif info["prov"]:
                output["provinces"][info["prov"]] = {
                    "stress_score": score,
                    "status": st,
                    "raw_interest": raw,
                }
        else:
            print("N/A")

    n = len(output["provinces"])
    with open("detresse_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ detresse_data.json sauvegardé — {n} provinces")
    if n == 0:
        print("⚠️ Aucune donnée (Trends rate-limited ?) — réessai au prochain run")
    else:
        print("\n🦫 Done!")


if __name__ == "__main__":
    run()
