import os
import json
import base64
import requests
from http.server import BaseHTTPRequestHandler

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_SHEET_WEBHOOK_URL = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL")

def send_tg_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})

def get_file_bytes(file_id):
    f_res = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}").json()
    file_path = f_res["result"]["file_path"]
    download_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
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
    url = f"[https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=](https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=){GEMINI_API_KEY}"
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64_image}}
            ]
        }]
    }
    res = requests.post(url, json=payload).json()
    raw_text = res["candidates"][0]["content"]["parts"][0]["text"].strip()
    raw_text = raw_text.replace("```json", "").replace("```", "").strip()
    return json.loads(raw_text)

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
                requests.post(GOOGLE_SHEET_WEBHOOK_URL, json=payload, headers={"Content-Type": "application/json"})

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
