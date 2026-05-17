import requests
import json
import time
import random
import re
import os
from datetime import datetime, timedelta

# ============================================
# BEAVER.WATCH — Kijiji Scraper v3.1
# ============================================

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ============================================
# SYSTÈME DE PRÉDICTIONS & APPRENTISSAGE
# ============================================

def load_predictions(filename='kijiji_predictions.json'):
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_predictions(predictions, filename='kijiji_predictions.json'):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(predictions, f, ensure_ascii=False, indent=2)

def check_past_predictions(predictions, current_score):
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
            print(f"\n🎯 VÉRIFICATION PRÉDICTION :")
            print(f"   Prédit le : {pred['date_prediction']}")
            print(f"   Score prédit : {pred['score_predit']}")
            print(f"   Score réel : {current_score}")
            print(f"   Écart : {ecart_pct}%")
            print(f"   Résultat : {pred['resultat']}")
    return verified

def save_new_prediction(predictions, claude_analysis, current_score):
    if not claude_analysis or not claude_analysis.get("score_predit_4_semaines"):
        return
    today = datetime.now()
    verification_date = (today + timedelta(days=28)).strftime("%Y-%m-%d")
    new_pred = {
        "indicateur": "kijiji",
        "date_prediction": today.strftime("%Y-%m-%d"),
        "date_verification": verification_date,
        "score_actuel": current_score,
        "score_predit": claude_analysis["score_predit_4_semaines"],
        "niveau_alerte_predit": claude_analysis.get("niveau_alerte"),
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
    print(f"\n📌 Prédiction sauvegardée :")
    print(f"   Score prédit : {new_pred['score_predit']}")
    print(f"   Vérification le : {verification_date}")

def get_predictions_feedback(predictions):
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

# Mots-clés par ville
CATEGORIES = {
    "outils": {"label_fr": "Outils & Équipement", "label_en": "Tools & Equipment",
        "emoji": "🔧", "weight": 0.35,
        "keywords": {"montreal": "outils", "toronto": "tools", "vancouver": "tools"}},
    "electronique": {"label_fr": "Consoles & Électronique", "label_en": "Electronics & Gaming",
        "emoji": "🎮", "weight": 0.25,
        "keywords": {"montreal": "electronique", "toronto": "electronics", "vancouver": "electronics"}},
    "transfert-bail": {"label_fr": "Transfert de Bail", "label_en": "Lease Transfers",
        "emoji": "🏠", "weight": 0.25,
        "keywords": {"montreal": "transfert-bail", "toronto": "lease-takeover", "vancouver": "lease-takeover"}},
    "bijoux": {"label_fr": "Bijoux & Montres", "label_en": "Jewelry & Watches",
        "emoji": "💍", "weight": 0.15,
        "keywords": {"montreal": "bijoux", "toronto": "jewelry", "vancouver": "jewelry"}},
}

CITIES = {
    "montreal": {"id": "1700281", "name": "Montréal"},
    "toronto":  {"id": "1700272", "name": "Toronto"},
    "vancouver": {"id": "1700287", "name": "Vancouver"},
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# ============================================
# CLAUDE API
# ============================================

def analyze_with_claude(today_data, history):
    if not ANTHROPIC_API_KEY:
        print("No API key — skipping")
        return None
    print("\n🤖 Claude API analysis...")
    feedback = get_predictions_feedback(load_predictions())
    history_summary = {
        date: {"score": data.get("national_score"), "period": data.get("pay_period")}
        for date, data in list(history.items())[-30:]
    }
    national = today_data.get("national_score", 0)
    cities = {}
    for city, data in today_data.get("cities", {}).items():
        cities[city] = {
            "score": data.get("composite_score"),
            "categories": {k: {"change_pct": v.get("change_pct")}
                for k, v in data.get("categories", {}).items()}
        }
    prompt = (
        "Tu es l'analyste de BEAVER.WATCH, barometre de stress economique canadien.\n\n"
        f"AUJOURD'HUI ({datetime.now().strftime('%Y-%m-%d')}) :\n"
        f"Score national Kijiji : {national} / 1.0\n"
        f"Villes : {json.dumps(cities, ensure_ascii=False)}\n\n"
        f"HISTORIQUE 30 JOURS :\n{json.dumps(history_summary, ensure_ascii=False)}\n\n"
        "Genere UNIQUEMENT ce JSON sans backticks ni markdown:\n"
        "{\n"
        '  "analyse_fr": "3-4 phrases en francais. Analyse score du jour, compare historique, signal dominant.",\n'
        '  "analyse_en": "Same 3-4 sentences in English.",\n'
        '  "prediction_fr": "1-2 phrases. Prediction concrete 4-8 semaines.",\n'
        '  "prediction_en": "Same prediction in English.",\n'
        '  "signal_dominant": "Signal le plus fort en 5 mots max",\n'
        '  "niveau_alerte": "NORMAL ou TENSION ou CRISE",\n'
        '  "score_predit_4_semaines": 0.XX\n'
        "}\n\n"
        f"IMPORTANT: score_predit_4_semaines = nombre decimal reel entre 0.05 et 0.95. "
        f"Score actuel={national}. Ne mets JAMAIS 0.0 — estime un vrai chiffre."
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
            print("✅ Claude analysis done!")
            print(f"   Signal: {analysis.get('signal_dominant')}")
            print(f"   Alerte: {analysis.get('niveau_alerte')}")
            return analysis
        else:
            print(f"API error {response.status_code}: {response.text[:200]}")
            return None
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        return None
    except Exception as e:
        print(f"Claude error: {e}")
        return None

# ============================================
# PAYDAY
# ============================================

def get_pay_period():
    today = datetime.now().day
    if today <= 14:
        return {"period": "avant-paye", "period_en": "before-payday",
            "label_fr": "Avant la Paye", "label_en": "Before Payday",
            "day_of_month": today, "days_until_payday": 15 - today,
            "days_since_payday": None, "emoji": "📉"}
    return {"period": "apres-paye", "period_en": "after-payday",
        "label_fr": "Apres la Paye", "label_en": "After Payday",
        "day_of_month": today, "days_until_payday": None,
        "days_since_payday": today - 15, "emoji": "📈"}

def check_payday_alert(score, pay_period):
    if pay_period["period"] == "apres-paye":
        days = pay_period["days_since_payday"]
        if days >= 3 and score >= 0.65:
            return {"active": True, "level": "CRITIQUE",
                "message_fr": "ALERTE : Score critique apres la paye.",
                "message_en": "ALERT: Critical score after payday.",
                "days_since_payday": days}
        elif days >= 3 and score >= 0.50:
            return {"active": True, "level": "TENSION",
                "message_fr": "TENSION : Stress eleve apres la paye.",
                "message_en": "TENSION: High stress after payday.",
                "days_since_payday": days}
    return {"active": False, "level": None, "message_fr": None,
        "message_en": None, "days_since_payday": None}

# ============================================
# SCRAPER
# ============================================

def get_count(keyword, city_id):
    if city_id == "1700287":
        url = f"https://www.kijiji.ca/b-{keyword}/city-of-vancouver/k0l{city_id}"
    else:
        url = f"https://www.kijiji.ca/b-{city_id}/{keyword}/k0l{city_id}"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-CA,en;q=0.9,fr-CA;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.kijiji.ca/",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"HTTP {resp.status_code}")
            return None
        title_match = re.search(r'<title[^>]*>(.*?)</title>',
                                resp.text, re.IGNORECASE | re.DOTALL)
        if not title_match:
            print("no title")
            return None
        title = title_match.group(1).strip()
        print(f'"{title[:45]}"', end=" -> ")
        if any(x in title.lower() for x in ["aucune", "no ads", "0 annonce", "0 ad"]):
            print("0")
            return 0
        for raw in re.findall(r'\d[\d\s,\.]*\d|\d', title):
            cleaned = re.sub(r'[\s,\.]', '', raw)
            if cleaned.isdigit() and int(cleaned) > 0:
                print(int(cleaned))
                return int(cleaned)
        print("parse failed")
        return None
    except requests.Timeout:
        print("timeout")
        return None
    except Exception as e:
        print(f"error: {e}")
        return None

def calculate_score(current, baseline):
    if baseline is None or baseline == 0 or current is None:
        return None
    change = ((current - baseline) / baseline) * 100
    if change <= 0:
        return max(0.05, round(0.15 + (change / 200), 2))
    elif change <= 5:
        return round(0.15 + (change / 5) * 0.20, 2)
    elif change <= 15:
        return round(0.35 + ((change - 5) / 10) * 0.30, 2)
    elif change <= 30:
        return round(0.65 + ((change - 15) / 15) * 0.25, 2)
    else:
        return min(1.0, round(0.90 + ((change - 30) / 50) * 0.10, 2))

def get_status(score):
    if score is None:
        return {"label_fr": "N/A", "label_en": "N/A", "color": "gray", "emoji": "?"}
    if score < 0.35:
        return {"label_fr": "Normal", "label_en": "Normal", "color": "green", "emoji": "🟢"}
    if score < 0.65:
        return {"label_fr": "Tension", "label_en": "Tension", "color": "orange", "emoji": "🟠"}
    return {"label_fr": "CRISE", "label_en": "CRISIS", "color": "red", "emoji": "🔴"}

# ============================================
# MAIN
# ============================================

def run():
    print("🦫 BEAVER.WATCH — Kijiji v3.1 + Claude API")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 52)

    try:
        with open('kijiji_baseline.json', 'r') as f:
            baseline = json.load(f)
        print("✅ Baseline loaded")
    except FileNotFoundError:
        baseline = {}
        print("No baseline — creating")

    try:
        with open('kijiji_history.json', 'r') as f:
            history = json.load(f)
        print(f"✅ History: {len(history)} days")
    except FileNotFoundError:
        history = {}
        print("No history — starting fresh")

    pay_period = get_pay_period()
    print(f"\n💰 {pay_period['label_fr']} — Day {pay_period['day_of_month']}")

    output = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "version": "3.1",
        "pay_period": pay_period,
        "payday_alert": None,
        "cities": {},
        "national_score": None,
        "national_status": None,
        "claude_analysis": None,
    }

    all_city_scores = []
    city_composite_scores = {}

    for city_key, city_info in CITIES.items():
        print(f"\n📍 {city_info['name']}")
        city_result = {
            "name_fr": city_info['name'],
            "name_en": city_info['name'],
            "categories": {},
            "composite_score": None,
            "composite_status": None,
        }
        weighted_scores = []

        for cat_key, cat_info in CATEGORIES.items():
            delay = random.uniform(6, 12)
            time.sleep(delay)
            keyword = cat_info['keywords'][city_key]
            print(f"  [{delay:.0f}s] {keyword}: ", end="", flush=True)
            count = get_count(keyword, city_info['id'])
            bkey = f"{city_key}_{cat_key}"
            base_count = baseline.get(bkey)
            if base_count is None and count is not None:
                baseline[bkey] = count
                base_count = count
            score = calculate_score(count, base_count)
            change_pct = None
            if count is not None and base_count:
                change_pct = round(((count - base_count) / base_count) * 100, 1)
            city_result['categories'][cat_key] = {
                "label_fr": cat_info['label_fr'],
                "label_en": cat_info['label_en'],
                "emoji": cat_info['emoji'],
                "count": count,
                "baseline": base_count,
                "change_pct": change_pct,
                "score": score,
                "status": get_status(score),
                "weight": cat_info['weight'],
            }
            if score is not None:
                weighted_scores.append((score, cat_info['weight']))

        if weighted_scores:
            total_w = sum(w for _, w in weighted_scores)
            composite = round(sum(s * w for s, w in weighted_scores) / total_w, 2)
            city_result['composite_score'] = composite
            city_result['composite_status'] = get_status(composite)
            all_city_scores.append(composite)
            city_composite_scores[city_key] = composite
            print(f"\n  📊 {city_info['name']}: {composite} {get_status(composite)['emoji']}")

        output['cities'][city_key] = city_result

    if all_city_scores:
        national = round(sum(all_city_scores) / len(all_city_scores), 2)
        output['national_score'] = national
        output['national_status'] = get_status(national)
        print(f"\n🍁 NATIONAL: {national} {get_status(national)['emoji']}")
        output['payday_alert'] = check_payday_alert(national, pay_period)
        date_key = datetime.now().strftime("%Y-%m-%d")
        history[date_key] = {
            "national_score": national,
            "city_scores": city_composite_scores,
            "pay_period": pay_period['period'],
            "date": date_key,
        }
        predictions = load_predictions()
        verified = check_past_predictions(predictions, national)
        if verified:
            output['predictions_verifiees'] = verified
        claude_analysis = analyze_with_claude(output, history)
        if claude_analysis:
            output['claude_analysis'] = claude_analysis
            save_new_prediction(predictions, claude_analysis, national)
            save_predictions(predictions)
            output['prochaine_verification'] = (
                datetime.now() + timedelta(days=28)
            ).strftime("%Y-%m-%d")

    with open('kijiji_data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("\n✅ kijiji_data.json saved")

    with open('kijiji_baseline.json', 'w', encoding='utf-8') as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)
    print("✅ kijiji_baseline.json saved")

    with open('kijiji_history.json', 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print("✅ kijiji_history.json saved")

    predictions = load_predictions()
    verified_count = sum(1 for p in predictions if p.get("score_reel") is not None)
    correct_count = sum(1 for p in predictions if p.get("claude_avait_raison"))
    if verified_count > 0:
        print(f"\n📊 Précision Claude: {correct_count}/{verified_count} ({round(correct_count/verified_count*100,1)}%)")

    print("\n🦫 Done!")

if __name__ == "__main__":
    run()
