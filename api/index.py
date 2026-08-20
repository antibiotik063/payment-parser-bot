import os
import json
import base64
import re
import requests
from http.server import BaseHTTPRequestHandler

def clean_url(val):
    if not val:
        return ""
    val = str(val).strip().strip("'\"[]")
    m = re.search(r'\((https?://[^)]+)\)', val)
    if m:
        return m.group(1).strip()
    m2 = re.search(r'https?://[^\s\]\)\"]+', val)
    if m2:
        return m2.group(0).strip()
    return val

TELEGRAM_TOKEN = str(os.environ.get("TELEGRAM_TOKEN", "")).strip().strip("'\"[]")
GEMINI_API_KEY = str(os.environ.get("GEMINI_API_KEY", "")).strip().strip("'\"[]")
GOOGLE_SHEET_WEBHOOK_URL = clean_url(os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", ""))

def send_tg_message(chat_id, text):
    url = f"[https://api.telegram.org/bot](https://api.telegram.org/bot){TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})

def get_file_bytes(file_id):
    f_res = requests.get(f"[https://api.telegram.org/bot](https://api.telegram.org/bot){TELEGRAM_TOKEN}/getFile?file_id={file_id}").json()
    file_path = f_res["result"]["file_path"]
    download_url = f"[https://api.telegram.org/file/bot](https://api.telegram.org/file/bot){TELEGRAM_TOKEN}/{file_path}"
    return requests.get(download_url).content

def parse_receipt_with_ai(image_bytes):
    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    
    prompt = """
    Ты финансовый парсер. Проанализируй прикрепленное платежное поручение РФ и извлеки JSON строго следующего формата:
    {
      "date": "ДД.ММ.ГГГГ",
      "doc_number": "номер платежки",
      "payer": "Наименование плательщика",
      "beneficiary": "Наименование получателя",
      "purpose": "Назначение платежа",
      "amount": 1200000.00
    }
    Ответ дай строго валидным JSON без markdown-разметки (без ```json). Поле amount должно быть числом (float).
    """
    
    # Каскадный список моделей и версий API на случай смены версий Google
    candidate_endpoints = [
        ("v1beta", "gemini-2.0-flash"),
        ("v1beta", "gemini-1.5-flash-latest"),
        ("v1", "gemini-1.5-flash"),
        ("v1beta", "gemini-2.5-flash"),
        ("v1beta", "gemini-1.5-pro")
    ]
    
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64_image}}
            ]
        }]
    }
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }
    
    last_err = None
    for api_ver, model_name in candidate_endpoints:
        url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{model_name}:generateContent"
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=25)
            res_data = res.json()
            if "error" in res_data:
                last_err = res_data["error"].get("message", "API error")
                continue
            if "candidates" in res_data and len(res_data["candidates"]) > 0:
                raw_text = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                raw_text = re.sub(r"^```json\s*", "", raw_text, flags=re.MULTILINE)
                raw_text = re.sub(r"^```\s*", "", raw_text, flags=re.MULTILINE)
                raw_text = raw_text.strip()
                return json.loads(raw_text)
        except Exception as e:
            last_err = str(e)
            continue
            
    raise Exception(f"Google API Error: {last_err}")

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        update = json.loads(body.decode('utf-8'))

        if "message" in update and "photo" in update["message"]:
            chat_id = update["message"]["chat"]["id"]
            photo = update["message"]["photo"][-1]
            file_id = photo["file_id"]

            send_tg_message(chat_id, "⏳ Распознаю платежку...")

            try:
                img_bytes = get_file_bytes(file_id)
                data = parse_receipt_with_ai(img_bytes)

                rate_str = "0.50%"
                rate_val = 0.0050
                income = data["amount"] * rate_val

                payload = {
                    "date": data["date"],
                    "doc_num": str(data["doc_number"]),
                    "payer": data["payer"],
                    "beneficiary": data["beneficiary"],
                    "purpose": data["purpose"],
                    "amount": data["amount"],
                    "rate": rate_str
                }
                requests.post(GOOGLE_SHEET_WEBHOOK_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=15)

                msg = (
                    f"✅ <b>Платеж добавлен в Google Таблицу!</b>\n\n"
                    f"📅 <b>Дата:</b> {data['date']} (№ {data['doc_number']})\n"
                    f"🏢 <b>От кого:</b> {data['payer']}\n"
                    f"📥 <b>Кому:</b> {data['beneficiary']}\n"
                    f"💰 <b>Сумма:</b> {data['amount']:,.2f} ₽\n"
                    f"📈 <b>Доход (0.5%):</b> +{income:,.2f} ₽"
                )
                send_tg_message(chat_id, msg)

            except Exception as e:
                send_tg_message(chat_id, f"❌ Ошибка: {str(e)}")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
