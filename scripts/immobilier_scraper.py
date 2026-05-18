import json
import time
import io
import zipfile
import csv
import requests
from datetime import datetime

# ============================================
# BEAVER.WATCH — StatCan Immobilier Scraper
# Source : API WDS Statistique Canada (GRATUITE, sans clé)
# Tableau 18-10-0205 — Indice des prix des logements neufs
# ============================================
#
# MÊME MÉTHODE que statcan_scraper.py (chômage) qui MARCHE :
# on demande le CSV complet du tableau, on le parse, on
# extrait l'indice par province. Pas de devinette de vecteur.
#
# Logique du signal :
# L'indice des prix des logements neufs (NHPI) monte =
# pression immobilière = stress pour les ménages.
# On regarde le NIVEAU récent ET la TENDANCE (12 mois).
# Une hausse rapide = stress croissant dans la région.
#
# GARDE-FOU : l'indice NHPI tourne autour de 80-200
# (base déc 2016 = 100). Tout hors 50-400 est REJETÉ
# (aberration = on n'affiche pas de faux chiffre).
# ============================================

PID = "18100205"  # New housing price index, monthly
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


def trend_to_stress(yoy_pct, level):
    """
    Convertit la variation annuelle (%) de l'indice des prix
    en score de stress 0-1.
    Logique : hausse rapide des prix = stress immobilier.
    - Prix qui baissent (yoy < 0)         -> stress faible
    - Prix stables (~0-2%)                -> stress modéré-bas
    - Hausse 2-6%                          -> stress moyen
    - Hausse 6-12%                         -> stress élevé
    - Hausse > 12%                         -> stress très élevé
    """
    if yoy_pct is None:
        return None
    if yoy_pct <= -2.0:
        return 0.15
    if yoy_pct <= 1.0:
        return round(0.15 + (yoy_pct + 2.0) / 3.0 * 0.15, 2)   # 0.15 -> 0.30
    if yoy_pct <= 4.0:
        return round(0.30 + (yoy_pct - 1.0) / 3.0 * 0.20, 2)   # 0.30 -> 0.50
    if yoy_pct <= 8.0:
        return round(0.50 + (yoy_pct - 4.0) / 4.0 * 0.25, 2)   # 0.50 -> 0.75
    if yoy_pct <= 14.0:
        return round(0.75 + (yoy_pct - 8.0) / 6.0 * 0.20, 2)   # 0.75 -> 0.95
    return 0.95


def status_from_score(s):
    if s is None:
        return {"fr": "N/A", "en": "N/A", "emoji": "❓"}
    if s < 0.30:
        return {"fr": "Détendu", "en": "Relaxed", "emoji": "🟢"}
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
    Télécharge le ZIP, parse le CSV, extrait pour chaque province :
    l'indice le plus récent ET l'indice ~12 mois avant,
    pour calculer la variation annuelle (year-over-year).
    On garde la série 'Total (house and land)', maison + terrain.
    """
    try:
        r = requests.get(zip_url, timeout=90)
        if r.status_code != 200:
            print(f"  ZIP HTTP {r.status_code}")
            return {}
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        csv_name = [n for n in zf.namelist()
                    if n.lower().endswith(".csv") and "MetaData" not in n]
        if not csv_name:
            print("  Pas de CSV dans le ZIP")
            return {}

        # series[prov_en] = liste de (ref_date, value)
        series = {}
        with zf.open(csv_name[0]) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8-sig")
            reader = csv.DictReader(text)
            for row in reader:
                geo = (row.get("GEO") or "").strip()
                if geo not in PROV_NAMES:
                    continue
                # Type d'indice : on veut le total (maison + terrain)
                comp = (row.get("New housing price indexes") or "").strip()
                if comp and comp not in ("Total (house and land)", "House and land"):
                    continue
                ref = (row.get("REF_DATE") or "").strip()
                val = (row.get("VALUE") or "").strip()
                if not ref or not val:
                    continue
                try:
                    v = float(val)
                except ValueError:
                    continue
                # GARDE-FOU : NHPI plausible entre 50 et 400
                if v < 50 or v > 400:
                    continue
                series.setdefault(geo, []).append((ref, v))

        result = {}
        for geo, pts in series.items():
            if len(pts) < 2:
                continue
            pts.sort(key=lambda x: x[0])           # tri par date
            ref_latest, v_latest = pts[-1]
            # point ~12 mois avant (13 points avant si mensuel, sinon le plus ancien dispo)
            idx_year_ago = max(0, len(pts) - 13)
            ref_prev, v_prev = pts[idx_year_ago]
            if v_prev <= 0:
                continue
            yoy = round((v_latest - v_prev) / v_prev * 100, 1)
            result[geo] = {
                "index": v_latest,
                "yoy_pct": yoy,
                "ref": ref_latest,
                "ref_prev": ref_prev,
            }
        return result
    except zipfile.BadZipFile:
        print("  ZIP corrompu")
        return {}
    except Exception as e:
        print(f"  Erreur parse: {e}")
        return {}


def run():
    print("🦫 BEAVER.WATCH — StatCan Immobilier Scraper")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("🔍 Source : API WDS StatCan — tableau 18-10-0205 (prix logements neufs)")
    print("=" * 52)

    output = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "Statistique Canada — Indice des prix des logements neufs (tableau 18-10-0205)",
        "indicator": "indice_prix_logements_neufs_variation_annuelle",
        "provinces": {},
    }

    print("\n📡 Demande du lien de téléchargement StatCan...")
    link = get_csv_download_link()
    if not link:
        print("\n⚠️ Lien indisponible. Aucun chiffre produit (volontaire).")
        with open("immobilier_data.json", "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print("✅ immobilier_data.json sauvegardé (vide — honnête)")
        return

    print("  Lien obtenu ✓")
    print("\n📥 Téléchargement + analyse du tableau (30-60s)...")
    time.sleep(1)
    parsed = download_and_parse(link)

    if not parsed:
        print("\n⚠️ Aucune donnée valide. Aucun chiffre produit (volontaire).")
        with open("immobilier_data.json", "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print("✅ immobilier_data.json sauvegardé (vide — honnête)")
        return

    print("\n📊 Pression immobilière par province (variation annuelle des prix)")
    for geo_en, meta in PROV_NAMES.items():
        d = parsed.get(geo_en)
        if not d:
            print(f"  ⚠️ {meta['fr']}: non trouvé")
            continue
        yoy = d["yoy_pct"]
        score = trend_to_stress(yoy, d["index"])
        st = status_from_score(score)
        output["provinces"][meta["code"]] = {
            "name_fr": meta["fr"],
            "name_en": geo_en,
            "indice": d["index"],
            "variation_annuelle_pct": yoy,
            "stress_score": score,
            "status": st,
            "ref_period": d["ref"],
        }
        sign = "+" if yoy >= 0 else ""
        print(f"  {st['emoji']} {meta['fr']}: {sign}{yoy}%/an → stress {score}  ({d['ref']})")

    n = len(output["provinces"])
    with open("immobilier_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✅ immobilier_data.json sauvegardé — {n} provinces")
    if n == 0:
        print("\n⚠️ AUCUNE province validée — structure CSV à vérifier")
    else:
        print("\n🦫 Done!")


if __name__ == "__main__":
    run()
