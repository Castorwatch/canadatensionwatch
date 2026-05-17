import json
import time
import random
import os
import requests
from datetime import datetime, timedelta

# ============================================
# BEAVER.WATCH — Auto Theft Scraper v2
# Sources : Google Trends + Claude API
# AVEC système de prédictions
# ============================================

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ============================================
# SYSTÈME DE PRÉDICTIONS & APPRENTISSAGE
# ============================================

def load_predictions_auto(filename='auto_theft_predictions.json'):
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_predictions_auto(predictions, filename='auto_theft_predictions.json'):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(predictions, f, ensure_ascii=False, indent=2)

def check_past_predictions_auto(predictions, current_score):
    today = datetime.now().strftime("%Y-%m-%d")
    verified = []
    for pred in predictions:
        if pred.get("date_verification") == today and pred.get("score_reel") is None:
            ecart = round(current_score - pred["score_predit"], 3)
            ecart_pct = round((ecart / pred["score_predit"]) * 100, 1) if pred["score_predit"] else 0
            avait_raison = abs(ecart_pct) <= 15
            pred["score_reel"] = current_score
            pred["ecart"] = ecart
            pred["ecart_pct"] = ecart_pct
            pred["claude_avait_raison"] = avait_raison
            pred["resultat"] = "✅ CORRECT" if avait_raison else "❌ INCORRECT"
            pred["verifie_le"] = today
            verified.append(pred)
            print(f"\n🎯 VÉRIFICATION PRÉDICTION AUTO :")
            print(f"   Prédit le : {pred['date_prediction']}")
            print(f"   Score prédit : {pred['score_predit']}")
            print(f"   Score réel : {current_score}")
            print(f"   Écart : {ecart_pct}%")
            print(f"   Résultat : {pred['resultat']}")
    return verified

def save_new_prediction_auto(predictions, claude_analysis, current_score):
    if not claude_analysis or not claude_analysis.get("score_predit_4_semaines"):
        return
    today = datetime.now()
    verification_date = (today + timedelta(days=28)).strftime("%Y-%m-%d")
    new_pred = {
        "indicateur": "auto_theft",
        "date_prediction": today.strftime("%Y-%m-%d"),
        "date_verification": verification_date,
        "score_actuel": current_score,
        "score_predit": claude_analysis["score_predit_4_semaines"],
        "niveau_alerte_predit": claude_analysis.get("niveau_alerte"),
        "region_risque": claude_analysis.get("region_risque"),
        "signal_dominant": claude_analysis.get("signal_dominant"),
        "prediction_fr": claude_analysis.get("prediction_fr"),
        "prediction_en": claude_analysis.get("prediction_en"),
        "score_reel": None,
        "ecart": None,
        "ecart_pct": None,
        "claude_avait_raison": None,
        "resultat": None,
        "verifie_le": None,
    }
    predictions.append(new_pred)
    print(f"\n📌 Prédiction AUTO sauvegardée :")
    print(f"   Score prédit : {new_pred['score_predit']}")
    print(f"   Vérification le : {verification_date}")

def get_feedback_auto(predictions):
    verified = [p for p in predictions if p.get("score_reel") is not None]
    if not verified:
        return None
    recent = verified[-10:]
    correct = sum(1 for p in recent if p.get("claude_avait_raison"))
    taux = round((correct / len(recent)) * 100, 1)
    return {
        "total_verifications": len(verified),
        "taux_precision": taux,
        "recent_results": [
            {"date": p["date_prediction"], "predit": p["score_predit"],
             "reel": p["score_reel"], "ecart_pct": p["ecart_pct"],
             "correct": p["claude_avait_raison"]}
            for p in recent
        ]
    }

# Google Trends keywords
KEYWORDS = {
    "vol_auto": {
        "terms_fr": ["vol de voiture", "voiture volée", "tracker GPS voiture"],
        "terms_en": ["car theft canada", "stolen car", "auto theft"],
        "label_fr": "Vol d'Auto",
        "label_en": "Auto Theft",
        "emoji": "🚗",
        "weight": 0.50,
    },
    "assurance_auto": {
        "terms_fr": ["assurance auto vol", "réclamation assurance voiture"],
        "terms_en": ["car theft insurance claim", "auto theft insurance canada"],
        "label_fr": "Réclamations Assurance",
        "label_en": "Insurance Claims",
        "emoji": "📋",
        "weight": 0.25,
    },
    "securite_auto": {
        "terms_fr": ["antivol voiture", "traceur GPS voiture canada"],
        "terms_en": ["car security system canada", "gps tracker car theft"],
        "label_fr": "Sécurité Auto",
        "label_en": "Car Security",
        "emoji": "🔒",
        "weight": 0.25,
    },
}

REGIONS = {
    "canada":  {"geo": "CA",    "name_fr": "Canada",   "name_en": "Canada"},
    "ontario": {"geo": "CA-ON", "name_fr": "Ontario",  "name_en": "Ontario"},
    "quebec":  {"geo": "CA-QC", "name_fr": "Québec",   "name_en": "Quebec"},
    "bc":      {"geo": "CA-BC", "name_fr": "C.-B.",    "name_en": "BC"},
    "alberta": {"geo": "CA-AB", "name_fr": "Alberta",  "name_en": "Alberta"},
}

def get_trend_score(terms, geo="CA"):
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl='en-CA', tz=-300, timeout=(10,25), retries=2)
        time.sleep(random.uniform(10, 15))
        pytrends.build_payload(
            terms[:5], cat=0,
            timeframe='now 7-d', geo=geo, gprop=''
        )
        data = pytrends.interest_over_time()
        if data.empty:
            return None
        scores = [data[t].mean() for t in terms if t in data.columns]
        return round(sum(scores)/len(scores), 1) if scores else None
    except Exception as e:
        print(f"    Trends error: {e}")
        return None

def normalize_score(raw, baseline=25):
    if raw is None:
        return None
    if raw <= baseline:
        return round(max(0.05, (raw/baseline)*0.35), 2)
    excess = raw - baseline
    return round(min(1.0, 0.35 + (excess/(100-baseline))*0.65), 2)

def get_status(score):
    if score is None:
        return {"label_fr": "N/A", "label_en": "N/A", "color": "gray", "emoji": "❓"}
    if score < 0.35:
        return {"label_fr": "Normal", "label_en": "Normal", "color": "green", "emoji": "🟢"}
    if score < 0.65:
        return {"label_fr": "Tension", "label_en": "Tension", "color": "orange", "emoji": "🟠"}
    return {"label_fr": "CRITIQUE", "label_en": "CRITICAL", "color": "red", "emoji": "🔴"}

def analyze_with_claude(today_data, history):
    if not ANTHROPIC_API_KEY:
        print("No API key — skipping Claude analysis")
        return None

    print("\n🤖 Claude Auto Theft analysis...")

    feedback = get_feedback_auto(load_predictions_auto())

    history_summary = {
        date: {"score": data.get("national_score"), "trend": data.get("trend")}
        for date, data in list(history.items())[-30:]
    }

    national = today_data.get("national_score", 0)
    regions = {k: v.get("composite_score") for k, v in today_data.get("regions", {}).items()}
    keywords = {k: v.get("stress_score") for k, v in today_data.get("keywords", {}).items()}

    prompt = (
        "Tu es l'analyste de BEAVER.WATCH, barometre de stress canadien.\n\n"
        f"DONNEES VOL AUTO AUJOURD'HUI ({datetime.now().strftime('%Y-%m-%d')}) :\n"
        f"Score national : {national} / 1.0\n"
        f"Par region : {json.dumps(regions, ensure_ascii=False)}\n"
        f"Par categorie : {json.dumps(keywords, ensure_ascii=False)}\n\n"
        f"HISTORIQUE 30 JOURS :\n{json.dumps(history_summary, ensure_ascii=False)}\n\n"
        "Ces donnees viennent de Google Trends sur les recherches liees au vol d'auto au Canada.\n\n"
        "Genere UNIQUEMENT ce JSON sans backticks ni markdown:\n"
        "{\n"
        '  "analyse_fr": "3-4 phrases. Analyse le score, compare historique, identifie la region la plus a risque.",\n'
        '  "analyse_en": "Same in English.",\n'
        '  "prediction_fr": "1-2 phrases. Prediction concrete 4-8 semaines pour le vol auto canadien.",\n'
        '  "prediction_en": "Same in English.",\n'
        '  "region_risque": "Region la plus a risque en 2 mots",\n'
        '  "signal_dominant": "Signal principal en 5 mots max",\n'
        '  "niveau_alerte": "NORMAL ou TENSION ou CRITIQUE",\n'
        '  "score_predit_4_semaines": 0.XX\n'
        "}\n\n"
        f"IMPORTANT: score_predit_4_semaines = decimal reel entre 0.05 et 0.95. "
        f"Score actuel = {national}. JAMAIS 0.0."
    )

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY.strip(),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )

        if response.status_code == 200:
            content = response.json()["content"][0]["text"].strip()
            if "```" in content:
                parts = content.split("```")
                for part in parts:
                    if "{" in part:
                        content = part.strip()
                        if content.startswith("json"):
                            content = content[4:].strip()
                        break
            analysis = json.loads(content)
            print(f"✅ Claude Auto Theft done!")
            print(f"   Signal: {analysis.get('signal_dominant')}")
            print(f"   Region: {analysis.get('region_risque')}")
            print(f"   Alerte: {analysis.get('niveau_alerte')}")
            return analysis
        else:
            print(f"API error: {response.status_code}")
            return None

    except Exception as e:
        print(f"Claude error: {e}")
        return None

def run():
    print("🦫 BEAVER.WATCH — Auto Theft Scraper v2")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("🔍 Sources: Google Trends")
    print("=" * 52)

    try:
        with open('auto_theft_history.json', 'r') as f:
            history = json.load(f)
        print(f"✅ History: {len(history)} days")
    except FileNotFoundError:
        history = {}
        print("No history — starting fresh")

    output = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "Google Trends",
        "keywords": {},
        "regions": {},
        "national_score": None,
        "national_status": None,
        "claude_analysis": None,
        "trend": None,
    }

    print("\n📊 Keyword analysis — Canada")
    weighted_scores = []

    for cat_key, cat_info in KEYWORDS.items():
        terms = cat_info['terms_en']
        print(f"\n  🔍 {cat_key}...", end=" ", flush=True)
        raw = get_trend_score(terms, geo="CA")
        stress = normalize_score(raw)
        status = get_status(stress)
        if raw is not None:
            print(f"Raw: {raw} → Stress: {stress}")
        else:
            print("N/A")
        output['keywords'][cat_key] = {
            "label_fr": cat_info['label_fr'],
            "label_en": cat_info['label_en'],
            "emoji": cat_info['emoji'],
            "raw_score": raw,
            "stress_score": stress,
            "status": status,
            "weight": cat_info['weight'],
        }
        if stress is not None:
            weighted_scores.append((stress, cat_info['weight']))

    print("\n📍 Regional analysis")
    for reg_key, reg_info in REGIONS.items():
        print(f"\n  📍 {reg_info['name_en']}...", end=" ", flush=True)
        raw = get_trend_score(KEYWORDS['vol_auto']['terms_en'], geo=reg_info['geo'])
        stress = normalize_score(raw)
        status = get_status(stress)
        if raw is not None:
            print(f"Raw: {raw} → Stress: {stress}")
        else:
            print("N/A")
        output['regions'][reg_key] = {
            "name_fr": reg_info['name_fr'],
            "name_en": reg_info['name_en'],
            "composite_score": stress,
            "composite_status": status,
        }

    if weighted_scores:
        total_w = sum(w for _, w in weighted_scores)
        national = round(sum(s*w for s,w in weighted_scores)/total_w, 2)
        output['national_score'] = national
        output['national_status'] = get_status(national)
        print(f"\n🍁 NATIONAL Auto Theft: {national} {get_status(national)['emoji']}")

        yesterday = list(history.values())[-1] if history else None
        if yesterday and yesterday.get('national_score'):
            diff = national - yesterday['national_score']
            output['trend'] = round(diff, 2)
            trend_str = f"▲ +{diff:.2f}" if diff > 0 else f"▼ {diff:.2f}"
            print(f"   Tendance: {trend_str}")

        date_key = datetime.now().strftime("%Y-%m-%d")
        history[date_key] = {
            "national_score": national,
            "date": date_key,
            "trend": output['trend'],
        }

        predictions_auto = load_predictions_auto()
        verified = check_past_predictions_auto(predictions_auto, national)
        if verified:
            output['predictions_verifiees'] = verified

        claude = analyze_with_claude(output, history)
        if claude:
            output['claude_analysis'] = claude
            save_new_prediction_auto(predictions_auto, claude, national)
            save_predictions_auto(predictions_auto)
            output['prochaine_verification'] = (
                datetime.now() + timedelta(days=28)
            ).strftime("%Y-%m-%d")

    with open('auto_theft_data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("\n✅ auto_theft_data.json saved")

    with open('auto_theft_history.json', 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print("✅ auto_theft_history.json saved")

    predictions_auto = load_predictions_auto()
    vc = sum(1 for p in predictions_auto if p.get("score_reel") is not None)
    cc = sum(1 for p in predictions_auto if p.get("claude_avait_raison"))
    if vc > 0:
        print(f"\n📊 Précision Claude Auto: {cc}/{vc} ({round(cc/vc*100,1)}%)")

    print("\n🦫 Done!")

if __name__ == "__main__":
    run()
