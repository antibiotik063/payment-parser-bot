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
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"Error sending message: {e}")

def get_file_info(file_id):
    f_res = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}", timeout=10).json()
    file_path = f_res["result"]["file_path"]
    download_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    file_bytes = requests.get(download_url, timeout=20).content
    return file_bytes

def parse_receipt_with_ai(file_bytes, mime_type="image/jpeg"):
    b64_data = base64.b64encode(file_bytes).decode("utf-8")
    
    prompt = """
    Ты финансовый аналитик. Внимательно изучи прикрепленный документ (платежное поручение РФ) и извлеки JSON строго следующего формата:
    {
      "date": "ДД.ММ.ГГГГ",
      "doc_number": "номер платежки",
      "payer": "Наименование плательщика (ООО/ИП)",
      "beneficiary": "Наименование получателя (ООО/ИП)",
      "purpose": "Назначение платежа",
      "amount": 1200000.00
    }
    Правила:
    1. Ответь строго валидным JSON без markdown-разметки (без ```json и без ```).
    2. Поле amount должно быть числом (float).
    3. Если поле не удается определить, напиши пустую строку "".
    """
    
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
                {"inline_data": {"mime_type": mime_type, "data": b64_data}}
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
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"Bot webhook is active and running!")

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
        try:
            update = json.loads(body.decode('utf-8'))
        except Exception:
            self.send_response(200)
            self.end_headers()
            return

        message = update.get("message") or update.get("channel_post")
        if not message:
            self.send_response(200)
            self.end_headers()
            return

        chat_id = message["chat"]["id"]
        file_id = None
        mime_type = "image/jpeg"

        # 1. Если отправлено как сжатое фото
        if "photo" in message:
            file_id = message["photo"][-1]["file_id"]
            mime_type = "image/jpeg"
        # 2. Если отправлено как файл / документ (PNG, JPG, PDF)
        elif "document" in message:
            file_id = message["document"]["file_id"]
            doc_mime = message["document"].get("mime_type", "")
            if "pdf" in doc_mime:
                mime_type = "application/pdf"
            elif "png" in doc_mime:
                mime_type = "image/png"
            else:
                mime_type = "image/jpeg"
        # 3. Если отправлен текст (например /start)
        elif "text" in message:
            send_tg_message(chat_id, "👋 <b>Бот на связи!</b>\n\nОтправьте скриншот или PDF платежного поручения, и я сразу занесу его в Google Таблицу с расчетом комиссии.")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'OK')
            return

        if file_id:
            send_tg_message(chat_id, "⏳ Распознаю платежку...")
            try:
                file_bytes = get_file_info(file_id)
                data = parse_receipt_with_ai(file_bytes, mime_type=mime_type)

                rate_str = "0.50%"
                rate_val = 0.0050
                amount = float(data.get("amount", 0))
                income = amount * rate_val

                payload = {
                    "date": data.get("date", ""),
                    "doc_num": str(data.get("doc_number", "")),
                    "payer": data.get("payer", ""),
                    "beneficiary": data.get("beneficiary", ""),
                    "purpose": data.get("purpose", ""),
                    "amount": amount,
                    "rate": rate_str
                }
                
                if GOOGLE_SHEET_WEBHOOK_URL:
                    requests.post(GOOGLE_SHEET_WEBHOOK_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=15)

                msg = (
                    f"✅ <b>Платеж добавлен в Google Таблицу!</b>\n\n"
                    f"📅 <b>Дата:</b> {data.get('date', '—')} (№ {data.get('doc_number', '—')})\n"
                    f"🏢 <b>От кого:</b> {data.get('payer', '—')}\n"
                    f"📥 <b>Кому:</b> {data.get('beneficiary', '—')}\n"
                    f"💰 <b>Сумма:</b> {amount:,.2f} ₽\n"
                    f"📈 <b>Доход (0.5%):</b> +{income:,.2f} ₽"
                )
                send_tg_message(chat_id, msg)

            except Exception as e:
                send_tg_message(chat_id, f"❌ Ошибка: {str(e)}")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
