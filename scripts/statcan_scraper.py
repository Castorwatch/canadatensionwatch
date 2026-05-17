import json
import time
import requests
from datetime import datetime

# ============================================
# BEAVER.WATCH — StatCan Chômage Scraper
# Source : API WDS Statistique Canada (GRATUITE, sans clé)
# Tableau 14-10-0294 / 14-10-0385 — Chômage par RMR
# ============================================
#
# Cette API est publique et gratuite. Aucune clé requise.
# Endpoint : getDataFromVectorsAndLatestNPeriods
#
# Un "vecteur" StatCan = une série temporelle précise
# (ex: taux de chômage désaisonnalisé pour la RMR de Vancouver).
#
# Les vecteurs ci-dessous correspondent au taux de chômage
# par grande région métropolitaine (RMR), moyennes mobiles
# 3 mois désaisonnalisées (tableau 14-10-0385-01).
# Si un vecteur devient invalide, le scraper le signale
# et continue avec les autres (robuste).
# ============================================

WDS_URL = "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods"

# Vecteurs taux de chômage par RMR (14-10-0385, moyennes mobiles 3 mois désais.)
# Vérifiés via l'interface tableau StatCan. Région -> vectorId
REGIONS = {
    "vancouver":  {"vector": "v1230668279", "name_fr": "Grand Vancouver",  "name_en": "Greater Vancouver",  "prov": "BC"},
    "montreal":   {"vector": "v1230668155", "name_fr": "Grand Montréal",   "name_en": "Greater Montreal",   "prov": "QC"},
    "toronto":    {"vector": "v1230668231", "name_fr": "Grand Toronto",    "name_en": "Greater Toronto",    "prov": "ON"},
    "calgary":    {"vector": "v1230668263", "name_fr": "Calgary",          "name_en": "Calgary",            "prov": "AB"},
    "edmonton":   {"vector": "v1230668259", "name_fr": "Edmonton",         "name_en": "Edmonton",           "prov": "AB"},
    "ottawa":     {"vector": "v1230668215", "name_fr": "Ottawa-Gatineau",  "name_en": "Ottawa-Gatineau",    "prov": "ON"},
    "quebec":     {"vector": "v1230668143", "name_fr": "Ville de Québec",  "name_en": "Quebec City",        "prov": "QC"},
    "winnipeg":   {"vector": "v1230668247", "name_fr": "Winnipeg",         "name_en": "Winnipeg",           "prov": "MB"},
    "hamilton":   {"vector": "v1230668223", "name_fr": "Hamilton",         "name_en": "Hamilton",           "prov": "ON"},
    "halifax":    {"vector": "v1230668115", "name_fr": "Halifax",          "name_en": "Halifax",            "prov": "NS"},
}

# Provinces (tableau 14-10-0287, taux désaisonnalisé) — fallback robuste
PROVINCES = {
    "BC": "v2064705",
    "QC": "v2064430",
    "ON": "v2064547",
    "AB": "v2064666",
    "MB": "v2064508",
    "SK": "v2064587",
    "NS": "v2064310",
    "NB": "v2064349",
}


def fetch_vectors(vector_ids, periods=2):
    """Appelle l'API WDS StatCan (gratuite, sans clé)."""
    body = [{"vectorId": int(v.replace("v", "")), "latestN": periods} for v in vector_ids]
    try:
        r = requests.post(
            WDS_URL,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"  API HTTP {r.status_code}: {r.text[:160]}")
            return {}
        data = r.json()
        out = {}
        for item in data:
            if item.get("status") != "SUCCESS":
                continue
            obj = item.get("object", {})
            vid = "v" + str(obj.get("vectorId"))
            pts = obj.get("vectorDataPoint", [])
            if pts:
                latest = pts[-1]
                prev = pts[-2] if len(pts) > 1 else None
                out[vid] = {
                    "value": latest.get("value"),
                    "ref_period": latest.get("refPer"),
                    "prev_value": prev.get("value") if prev else None,
                }
        return out
    except requests.Timeout:
        print("  API timeout")
        return {}
    except Exception as e:
        print(f"  API error: {e}")
        return {}


def unemployment_to_stress(rate):
    """
    Convertit un taux de chômage (%) en score de stress 0-1.
    Repères Canada : ~5% = sain, ~7% = moyenne, ~9%+ = tendu, ~12%+ = grave.
    Échelle calibrée sur l'historique canadien réel.
    """
    if rate is None:
        return None
    if rate <= 4.0:
        return 0.10
    if rate <= 5.5:
        return round(0.10 + (rate - 4.0) / 1.5 * 0.15, 2)   # 0.10 -> 0.25
    if rate <= 7.0:
        return round(0.25 + (rate - 5.5) / 1.5 * 0.20, 2)   # 0.25 -> 0.45
    if rate <= 9.0:
        return round(0.45 + (rate - 7.0) / 2.0 * 0.25, 2)   # 0.45 -> 0.70
    if rate <= 12.0:
        return round(0.70 + (rate - 9.0) / 3.0 * 0.20, 2)   # 0.70 -> 0.90
    return round(min(1.0, 0.90 + (rate - 12.0) / 6.0 * 0.10), 2)


def status_from_score(s):
    if s is None:
        return {"fr": "N/A", "en": "N/A", "emoji": "❓"}
    if s < 0.30:
        return {"fr": "Sain", "en": "Healthy", "emoji": "🟢"}
    if s < 0.50:
        return {"fr": "Surveiller", "en": "Watch", "emoji": "🟡"}
    if s < 0.70:
        return {"fr": "Tension", "en": "Tension", "emoji": "🟠"}
    return {"fr": "Critique", "en": "Critical", "emoji": "🔴"}


def run():
    print("🦫 BEAVER.WATCH — StatCan Chômage Scraper")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("🔍 Source : API WDS Statistique Canada (gratuite)")
    print("=" * 52)

    output = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "Statistique Canada — Enquête sur la population active (tableau 14-10-0385 / 14-10-0287)",
        "indicator": "taux_chomage_desaisonnalise",
        "regions": {},
        "provinces": {},
    }

    # --- 1. RÉGIONS MÉTROPOLITAINES ---
    print("\n📊 Taux de chômage par région métropolitaine")
    region_vectors = [r["vector"] for r in REGIONS.values()]
    region_data = fetch_vectors(region_vectors, periods=2)
    time.sleep(1)

    for key, info in REGIONS.items():
        vid = info["vector"]
        dp = region_data.get(vid)
        if dp and dp.get("value") is not None:
            rate = float(dp["value"])
            prev = dp.get("prev_value")
            score = unemployment_to_stress(rate)
            st = status_from_score(score)
            trend = None
            if prev is not None:
                trend = round(rate - float(prev), 2)
            output["regions"][key] = {
                "name_fr": info["name_fr"],
                "name_en": info["name_en"],
                "prov": info["prov"],
                "taux_chomage": rate,
                "stress_score": score,
                "status": st,
                "trend": trend,
                "ref_period": dp.get("ref_period"),
            }
            arrow = ""
            if trend is not None:
                arrow = f" ({'▲ +' if trend > 0 else '▼ '}{trend} pt)"
            print(f"  {st['emoji']} {info['name_fr']}: {rate}% → stress {score}{arrow}")
        else:
            print(f"  ⚠️ {info['name_fr']}: vecteur {vid} indisponible")

    # --- 2. PROVINCES (fallback robuste) ---
    print("\n📍 Taux de chômage par province (fallback)")
    prov_vectors = list(PROVINCES.values())
    prov_data = fetch_vectors(prov_vectors, periods=2)

    for prov, vid in PROVINCES.items():
        dp = prov_data.get(vid)
        if dp and dp.get("value") is not None:
            rate = float(dp["value"])
            score = unemployment_to_stress(rate)
            st = status_from_score(score)
            output["provinces"][prov] = {
                "taux_chomage": rate,
                "stress_score": score,
                "status": st,
                "ref_period": dp.get("ref_period"),
            }
            print(f"  {st['emoji']} {prov}: {rate}% → stress {score}")
        else:
            print(f"  ⚠️ {prov}: vecteur {vid} indisponible")

    # --- 3. SAUVEGARDE ---
    with open("chomage_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    n_reg = len(output["regions"])
    n_prov = len(output["provinces"])
    print(f"\n✅ chomage_data.json sauvegardé")
    print(f"   {n_reg} régions · {n_prov} provinces")

    if n_reg == 0 and n_prov == 0:
        print("\n⚠️ AUCUNE donnée récupérée — vérifier les vecteurs StatCan")
    else:
        print("\n🦫 Done!")


if __name__ == "__main__":
    run()
