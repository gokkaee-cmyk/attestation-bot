import os
import urllib.request
import urllib.error
import json
from datetime import datetime
import base64

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
REPORT_EMAIL = os.getenv("REPORT_EMAIL", "gokkaee@gmail.com")

def send_report_email(excel_path: str, count: int, date_range: str = ""):
    with open(excel_path, "rb") as f:
        file_content = base64.b64encode(f.read()).decode("utf-8")
    filename = f"Сводный_отчёт_{datetime.now().strftime('%d%m%Y')}.xlsx"
    body = f"""Добрый день!
Во вложении сводный отчёт по аттестации сотрудников.
Количество аттестаций: {count}
Дата формирования: {datetime.now().strftime('%d.%m.%Y %H:%M')}
{f'Период: {date_range}' if date_range else ''}
Отчёт сформирован автоматически ботом аттестации MDLZ."""
    payload = json.dumps({
        "from": "Аттестация MDLZ <onboarding@resend.dev>",
        "to": [REPORT_EMAIL],
        "subject": f"Сводный отчёт аттестации — {datetime.now().strftime('%d.%m.%Y')}",
        "text": body,
        "attachments": [
            {
                "filename": filename,
                "content": file_content,
            }
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise Exception(f"Resend {e.code}: {error_body}")
