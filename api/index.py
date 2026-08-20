import os
import json
import base64
import re
import traceback
import requests
from http.server import BaseHTTPRequestHandler

def sanitize_url(raw):
    if not raw:
        return ""
    s = str(raw).strip()
    s = re.sub(r'\[.*?\]\((https?://.*?)\)', r'\1', s)
    s = re.sub(r'\[.*?\]\((.*?)\)', r'\1', s)
    s = s.replace("[", "").replace("]", "").replace("'", "").replace('"', "").strip()
    return s

def sanitize_str(raw):
    if not raw:
        return ""
    return str(raw).strip().strip("'\"[]")

TELEGRAM_TOKEN = sanitize_str(os.environ.get("TELEGRAM_TOKEN", ""))
GEMINI_API_KEY = sanitize_str(os.environ.get("GEMINI_API_KEY", ""))
GOOGLE_SHEET_WEBHOOK_URL = sanitize_url(os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", ""))

TG_API_HOST = "https://" + "api" + ".telegram.org"
GEMINI_API_HOST = "https://" + "generativelanguage" + ".googleapis.com"

def send_tg_message(chat_id, text):
    if not TELEGRAM_TOKEN or not chat_id:
        return
    url = f"{TG_API_HOST}/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"Error sending TG message: {e}")

def get_file_info(file_id):
    url = f"{TG_API_HOST}/bot{TELEGRAM_TOKEN}/getFile"
    f_res = requests.get(url, params={"file_id": file_id}, timeout=10).json()
    file_path = f_res.get("result", {}).get("file_path")
    if not file_path:
        raise Exception("Не удалось получить путь к файлу из Telegram.")
    download_url = f"{TG_API_HOST}/file/bot{TELEGRAM_TOKEN}/{file_path}"
    return requests.get(download_url, timeout=25).content

def parse_receipt_with_ai(file_bytes, mime_type="image/jpeg"):
    b64_data = base64.b64encode(file_bytes).decode("utf-8")
    prompt = """
Ты финансовый аналитик. Внимательно изучи платежное поручение РФ и извлеки JSON строго следующего формата:
{
  "date": "ДД.ММ.ГГГГ",
  "doc_number": "номер платежки",
  "payer": "Наименование плательщика",
  "beneficiary": "Наименование получателя",
  "purpose": "Назначение платежа",
  "amount": 1200000.00
}
Ответ дай строго валидным JSON без markdown-разметки (без ```json). Поле amount — число (float).
"""
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": b64_data}}
            ]
        }]
    }

    # 1. Автоматический запрос списка доступных моделей для вашего ключа
    available_models = []
    try:
        list_url = f"{GEMINI_API_HOST}/v1beta/models"
        list_res = requests.get(list_url, headers=headers, params={"key": GEMINI_API_KEY}, timeout=10)
        list_data = list_res.json()
        if "models" in list_data:
            for m in list_data["models"]:
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    m_name = m.get("name", "").replace("models/", "")
                    available_models.append(m_name)
    except Exception:
        pass

    # Приоритет моделей
    candidate_models = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-flash-latest", "gemini-1.5-pro"]
    if available_models:
        candidate_models = [m for m in available_models if "flash" in m] + [m for m in available_models if "pro" in m] + available_models

    last_error = "Неизвестная ошибка"
    for model_name in candidate_models:
        gen_url = f"{GEMINI_API_HOST}/v1beta/models/{model_name}:generateContent"
        try:
            res = requests.post(gen_url, json=payload, headers=headers, params={"key": GEMINI_API_KEY}, timeout=30)
            res_data = res.json()
            if "error" in res_data:
                last_error = res_data["error"].get("message", "API Error")
                continue
            if "candidates" in res_data and len(res_data["candidates"]) > 0:
                raw_text = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                raw_text = re.sub(r"^```json\s*", "", raw_text, flags=re.MULTILINE)
                raw_text = re.sub(r"^```\s*", "", raw_text, flags=re.MULTILINE)
                raw_text = raw_text.strip()
                return json.loads(raw_text)
        except Exception as e:
            last_error = str(e)
            continue

    raise Exception(f"Google API Error: {last_error}")

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"Bot webhook is active and running!")

    def do_POST(self):
        chat_id = None
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            update = json.loads(body.decode('utf-8'))
            
            message = update.get("message") or update.get("edited_message") or update.get("channel_post")
            if not message:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'OK')
                return

            chat_id = message.get("chat", {}).get("id")
            if not chat_id:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'OK')
                return

            # Ответ на текстовые команды
            if "text" in message:
                send_tg_message(chat_id, "👋 <b>Бот на связи!</b>\n\nОтправьте скриншот или PDF платежного поручения, и я сразу занесу его в Google Таблицу.")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'OK')
                return

            # Обработка вложений
            file_id = None
            mime_type = "image/jpeg"
            if "photo" in message:
                file_id = message["photo"][-1]["file_id"]
                mime_type = "image/jpeg"
            elif "document" in message:
                file_id = message["document"]["file_id"]
                doc_mime = message["document"].get("mime_type", "")
                if "pdf" in doc_mime:
                    mime_type = "application/pdf"
                elif "png" in doc_mime:
                    mime_type = "image/png"
                else:
                    mime_type = "image/jpeg"

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
                except Exception as parse_err:
                    send_tg_message(chat_id, f"❌ Ошибка обработки: {str(parse_err)}")

        except Exception as global_err:
            print(f"Global server error: {traceback.format_exc()}")
            if chat_id:
                send_tg_message(chat_id, f"⚠️ Сбой сервера: {str(global_err)}")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
