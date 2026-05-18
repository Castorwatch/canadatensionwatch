import json
import time
import io
import zipfile
import csv
import requests
from datetime import datetime

# ============================================
# BEAVER.WATCH — Scraper Vitesse Démographique
# Source : API WDS StatCan (GRATUITE, sans clé)
# Tableau 17-10-0008 — Composantes croissance démographique
# ============================================
#
# MÊME MÉTHODE CSV que statcan / immobilier (qui MARCHENT).
#
# SIGNAL : ce n'est PAS le niveau de population, c'est
# la VITESSE de changement d'une province. Un changement
# rapide (forte croissance via migration) crée un stress
# d'absorption : pression logement, services, emploi,
# cohésion — AVANT que la crise soit visible.
#
# CADRAGE STRICT (plan, Niveau 2c) :
# on mesure le RYTHME, jamais l'origine des personnes.
# Une absorption au bon rythme = immigration réussie.
# Une absorption trop rapide pour la capacité d'accueil
# = signal de tension structurelle 3-5 ans en avance.
#
# GARDE-FOU : taux de croissance annuel plausible
# entre -5% et +10%. Hors plage = rejeté.
# ============================================

PID = "17100008"
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


def velocity_to_stress(growth_pct):
    """
    Vitesse de croissance démographique annuelle (%) -> stress 0-1.
    Logique d'absorption :
    - Décroissance forte (zone qui se vide) = stress modéré
    - Croissance lente/normale (~0.5-1.5%) = sain
    - Croissance rapide (2-3%) = pression d'absorption
    - Croissance très rapide (>3%) = stress structurel élevé
      (logement/services/cohésion dépassés)
    """
    if growth_pct is None:
        return None
    if growth_pct <= -2.0:
        return 0.45            # zone qui se vide : déclin
    if growth_pct <= 0.0:
        return round(0.30 + (-growth_pct) / 2.0 * 0.15, 2)   # 0.30 -> 0.45
    if growth_pct <= 1.5:
        return round(0.15 + growth_pct / 1.5 * 0.10, 2)      # 0.15 -> 0.25 (sain)
    if growth_pct <= 3.0:
        return round(0.25 + (growth_pct - 1.5) / 1.5 * 0.30, 2)  # 0.25 -> 0.55
    if growth_pct <= 5.0:
        return round(0.55 + (growth_pct - 3.0) / 2.0 * 0.30, 2)  # 0.55 -> 0.85
    return round(min(1.0, 0.85 + (growth_pct - 5.0) / 5.0 * 0.15), 2)


def status_from_score(s):
    if s is None:
        return {"fr": "N/A", "en": "N/A", "emoji": "❓"}
    if s < 0.30:
        return {"fr": "Absorption saine", "en": "Healthy", "emoji": "🟢"}
    if s < 0.50:
        return {"fr": "Surveiller", "en": "Watch", "emoji": "🟡"}
    if s < 0.70:
        return {"fr": "Pression", "en": "Pressure", "emoji": "🟠"}
    return {"fr": "Stress structurel", "en": "Structural stress", "emoji": "🔴"}


def get_csv_download_link():
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
    17-10-0008 contient des COMPOSANTES (immigrants, NPR,
    migration interprov., naissances, décès), pas forcément
    une ligne 'Population'. On calcule donc le flux entrant
    net annuel = Immigrants + Net non-permanent residents
    + Net interprovincial migration, rapporté à la
    population (si dispo) sinon en valeur brute normalisée.
    Auto-diagnostic : logue les composantes trouvées.
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

        # On collecte par (geo, ref) les composantes utiles
        data = {}            # (geo, ref) -> {comp: value}
        comp_col = None
        comps_seen = set()

        with zf.open(csv_name[0]) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8-sig")
            reader = csv.DictReader(text)
            # Détecter la colonne des composantes (varie selon tableau)
            if reader.fieldnames:
                for cand in ("Components of population growth",
                             "Estimates", "Components"):
                    if cand in reader.fieldnames:
                        comp_col = cand
                        break
            for row in reader:
                geo = (row.get("GEO") or "").strip()
                if geo not in PROV_NAMES:
                    continue
                comp = (row.get(comp_col) or "").strip() if comp_col else ""
                comps_seen.add(comp)
                ref = (row.get("REF_DATE") or "").strip()
                val = (row.get("VALUE") or "").strip()
                if not ref or not val:
                    continue
                try:
                    v = float(val)
                except ValueError:
                    continue
                data.setdefault((geo, ref), {})[comp] = v

        # Diagnostic : montrer les composantes disponibles
        sample = sorted([c for c in comps_seen if c])[:12]
        print(f"  Composantes détectées : {sample}")

        # Pour chaque province, prendre la période la plus récente
        # et sommer les flux entrants nets.
        latest_ref = {}
        for (geo, ref) in data:
            if geo not in latest_ref or ref > latest_ref[geo]:
                latest_ref[geo] = ref

        IMMI_KEYS = ["Immigrants"]
        NPR_KEYS = ["Net non-permanent residents",
                    "Net emigration"]  # NPR principal
        INTERPROV_KEYS = ["Net interprovincial migration"]
        POP_KEYS = ["Population at July 1", "Population on July 1",
                    "Population"]

        result = {}
        for geo, ref in latest_ref.items():
            comps = data.get((geo, ref), {})
            if not comps:
                continue

            def pick(keys):
                for k in keys:
                    if k in comps and comps[k] is not None:
                        return comps[k]
                return None

            immi = pick(IMMI_KEYS) or 0
            npr = comps.get("Net non-permanent residents") or 0
            interp = pick(INTERPROV_KEYS) or 0
            pop = pick(POP_KEYS)

            inflow = immi + npr + interp  # flux migratoire net
            if inflow == 0 and immi == 0:
                continue

            if pop and pop > 0:
                # taux de renouvellement migratoire annuel en %
                rate = round(inflow / pop * 100, 2)
            else:
                # pas de pop dispo : normaliser grossièrement
                # (immigrants pour 1000 ~ converti en % approx)
                rate = round(inflow / 100000.0, 2)

            # GARDE-FOU : taux plausible -5% à +10%
            if rate < -5 or rate > 10:
                continue

            result[geo] = {
                "growth_pct": rate,
                "ref": ref,
                "ref_prev": ref,
                "pop": int(pop) if pop else 0,
            }
        return result
    except zipfile.BadZipFile:
        print("  ZIP corrompu")
        return {}
    except Exception as e:
        print(f"  Erreur parse: {e}")
        return {}


def run():
    print("🦫 BEAVER.WATCH — Scraper Vitesse Démographique")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("🔍 Source : API WDS StatCan — tableau 17-10-0008")
    print("🧭 Mesure le RYTHME de changement, jamais l'origine")
    print("=" * 52)

    output = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "Statistique Canada — Composantes de la croissance démographique (tableau 17-10-0008)",
        "indicator": "vitesse_changement_demographique_pct_annuel",
        "note": "Mesure le rythme d'absorption d'une zone. "
                "Rythme trop rapide pour la capacité d'accueil = stress "
                "structurel 3-5 ans avant la crise. Jamais l'origine.",
        "provinces": {},
    }

    print("\n📡 Demande du lien de téléchargement StatCan...")
    link = get_csv_download_link()
    if not link:
        print("\n⚠️ Lien indisponible. Aucun chiffre produit (volontaire).")
        with open("demographie_data.json", "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print("✅ demographie_data.json sauvegardé (vide — honnête)")
        return

    print("  Lien obtenu ✓")
    print("\n📥 Téléchargement + analyse (30-60s)...")
    time.sleep(1)
    parsed = download_and_parse(link)

    if not parsed:
        print("\n⚠️ Aucune donnée valide. Aucun chiffre produit (volontaire).")
        with open("demographie_data.json", "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print("✅ demographie_data.json sauvegardé (vide — honnête)")
        return

    print("\n📊 Vitesse de changement démographique par province")
    for geo_en, meta in PROV_NAMES.items():
        d = parsed.get(geo_en)
        if not d:
            print(f"  ⚠️ {meta['fr']}: non trouvé")
            continue
        g = d["growth_pct"]
        score = velocity_to_stress(g)
        st = status_from_score(score)
        output["provinces"][meta["code"]] = {
            "name_fr": meta["fr"],
            "name_en": geo_en,
            "croissance_annuelle_pct": g,
            "stress_score": score,
            "status": st,
            "ref_period": d["ref"],
            "population": d["pop"],
        }
        sign = "+" if g >= 0 else ""
        print(f"  {st['emoji']} {meta['fr']}: {sign}{g}%/an → stress {score}  ({d['ref']})")

    n = len(output["provinces"])
    with open("demographie_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✅ demographie_data.json sauvegardé — {n} provinces")
    print("\n🦫 Done!" if n else "\n⚠️ AUCUNE province validée — structure CSV à vérifier")


if __name__ == "__main__":
    run()
