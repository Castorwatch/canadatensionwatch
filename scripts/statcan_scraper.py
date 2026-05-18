import json
import time
import io
import zipfile
import csv
import requests
from datetime import datetime

# ============================================
# BEAVER.WATCH — StatCan Chômage Scraper v2
# Source : API WDS Statistique Canada (GRATUITE, sans clé)
# Tableau 14-10-0287 — Caractéristiques population active
# ============================================
#
# APPROCHE v2 (fiable) :
# Au lieu de DEVINER des numéros de vecteurs (échec v1),
# on demande à StatCan le lien de téléchargement CSV complet
# du tableau 14-10-0287, on parse le ZIP, et on extrait
# directement le taux de chômage par province.
#
# GARDE-FOU : tout taux hors plage 0-30% est REJETÉ
# (un vrai taux de chômage ne dépasse jamais ~25% au Canada).
# Si la donnée semble aberrante → on ne l'affiche PAS.
# Mieux vaut pas de chiffre qu'un faux chiffre.
# ============================================

PID = "14100287"  # Labour force characteristics, monthly, seasonally adjusted
CSV_LINK_URL = f"https://www150.statcan.gc.ca/t1/wds/rest/getFullTableDownloadCSV/{PID}/en"

PROV_NAMES = {
    "Newfoundland and Labrador": {"code": "NL", "fr": "Terre-Neuve-et-Labrador"},
    "Prince Edward Island":      {"code": "PE", "fr": "Île-du-Prince-Édouard"},
    "Nova Scotia":               {"code": "NS", "fr": "Nouvelle-Écosse"},
    "New Brunswick":             {"code": "NB", "fr": "Nouveau-Brunswick"},
    "Quebec":                    {"code": "QC", "fr": "Québec"},
    "Ontario":                   {"code": "ON", "fr": "Ontario"},
    "Manitoba":                  {"code": "MB", "fr": "Manitoba"},
    "Saskatchewan":              {"code": "SK", "fr": "Saskatchewan"},
    "Alberta":                   {"code": "AB", "fr": "Alberta"},
    "British Columbia":          {"code": "BC", "fr": "Colombie-Britannique"},
}


def unemployment_to_stress(rate):
    """Taux de chômage (%) -> score stress 0-1. Calibré historique réel CA."""
    if rate is None:
        return None
    if rate <= 4.0:
        return 0.10
    if rate <= 5.5:
        return round(0.10 + (rate - 4.0) / 1.5 * 0.15, 2)
    if rate <= 7.0:
        return round(0.25 + (rate - 5.5) / 1.5 * 0.20, 2)
    if rate <= 9.0:
        return round(0.45 + (rate - 7.0) / 2.0 * 0.25, 2)
    if rate <= 12.0:
        return round(0.70 + (rate - 9.0) / 3.0 * 0.20, 2)
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


def get_csv_download_link():
    """Demande à StatCan le lien ZIP du tableau complet."""
    try:
        r = requests.get(CSV_LINK_URL, timeout=30)
        if r.status_code != 200:
            print(f"  Lien API HTTP {r.status_code}")
            return None
        j = r.json()
        if j.get("status") == "SUCCESS":
            return j.get("object")
        print(f"  Statut API: {j.get('status')}")
        return None
    except Exception as e:
        print(f"  Erreur lien: {e}")
        return None


def download_and_parse(zip_url):
    """
    Télécharge le ZIP, parse le CSV, extrait le DERNIER taux de
    chômage désaisonnalisé par province.
    Retourne {prov_en: rate} en ne gardant QUE les taux plausibles.
    """
    try:
        r = requests.get(zip_url, timeout=90)
        if r.status_code != 200:
            print(f"  ZIP HTTP {r.status_code}")
            return {}
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        csv_name = [n for n in zf.namelist() if n.lower().endswith(".csv") and "MetaData" not in n]
        if not csv_name:
            print("  Pas de CSV dans le ZIP")
            return {}

        latest = {}  # prov_en -> (ref_date, rate)
        with zf.open(csv_name[0]) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8-sig")
            reader = csv.DictReader(text)
            for row in reader:
                geo = (row.get("GEO") or "").strip()
                if geo not in PROV_NAMES:
                    continue
                # On veut : Unemployment rate, both sexes, 15 years and over,
                # seasonally adjusted (Estimate)
                charac = (row.get("Labour force characteristics") or "").strip()
                if charac != "Unemployment rate":
                    continue
                sex = (row.get("Gender") or row.get("Sex") or "").strip()
                if sex not in ("Both sexes", "Total - Gender", "Total", ""):
                    continue
                age = (row.get("Age group") or "").strip()
                if age not in ("15 years and over", "15 years and over and over", ""):
                    continue
                dtype = (row.get("Statistics") or row.get("Data type") or "").strip()
                # On accepte l'estimation (pas l'erreur-type)
                if dtype and "Estimate" not in dtype and dtype not in ("Seasonally adjusted",):
                    continue
                ref = (row.get("REF_DATE") or "").strip()
                val = (row.get("VALUE") or "").strip()
                if not ref or not val:
                    continue
                try:
                    rate = float(val)
                except ValueError:
                    continue
                # GARDE-FOU ANTI-ABERRATION : taux plausible 0-30%
                if rate < 0 or rate > 30:
                    continue
                prev = latest.get(geo)
                if prev is None or ref > prev[0]:
                    latest[geo] = (ref, rate)

        return {g: {"rate": v[1], "ref": v[0]} for g, v in latest.items()}
    except zipfile.BadZipFile:
        print("  ZIP corrompu")
        return {}
    except Exception as e:
        print(f"  Erreur parse: {e}")
        return {}


def run():
    print("🦫 BEAVER.WATCH — StatCan Chômage Scraper v2")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("🔍 Source : API WDS StatCan — tableau 14-10-0287 (CSV complet)")
    print("=" * 52)

    output = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "Statistique Canada — Enquête sur la population active (tableau 14-10-0287, désaisonnalisé)",
        "indicator": "taux_chomage",
        "provinces": {},
    }

    print("\n📡 Demande du lien de téléchargement StatCan...")
    link = get_csv_download_link()
    if not link:
        print("\n⚠️ Impossible d'obtenir le lien. Aucun chiffre produit (volontaire).")
        with open("chomage_data.json", "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print("✅ chomage_data.json sauvegardé (vide — honnête)")
        return

    print(f"  Lien obtenu ✓")
    print("\n📥 Téléchargement + analyse du tableau (peut prendre 30-60s)...")
    time.sleep(1)
    parsed = download_and_parse(link)

    if not parsed:
        print("\n⚠️ Aucune donnée valide extraite. Aucun chiffre produit (volontaire).")
        with open("chomage_data.json", "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print("✅ chomage_data.json sauvegardé (vide — honnête)")
        return

    print(f"\n📊 Taux de chômage par province (données réelles validées)")
    for geo_en, meta in PROV_NAMES.items():
        d = parsed.get(geo_en)
        if not d:
            print(f"  ⚠️ {meta['fr']}: non trouvé")
            continue
        rate = d["rate"]
        score = unemployment_to_stress(rate)
        st = status_from_score(score)
        output["provinces"][meta["code"]] = {
            "name_fr": meta["fr"],
            "name_en": geo_en,
            "taux_chomage": rate,
            "stress_score": score,
            "status": st,
            "ref_period": d["ref"],
        }
        print(f"  {st['emoji']} {meta['fr']}: {rate}% → stress {score}  ({d['ref']})")

    n = len(output["provinces"])
    with open("chomage_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✅ chomage_data.json sauvegardé — {n} provinces")

    if n == 0:
        print("\n⚠️ AUCUNE province validée — structure CSV à vérifier")
    else:
        print("\n🦫 Done!")


if __name__ == "__main__":
    run()
