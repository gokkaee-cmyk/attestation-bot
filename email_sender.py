import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime


GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
REPORT_EMAIL = os.getenv("REPORT_EMAIL", "tamaeva27@gmail.com")


def send_report_email(excel_path: str, count: int, date_range: str = ""):
    msg = MIMEMultipart()
    msg["From"] = GMAIL_USER
    msg["To"] = REPORT_EMAIL
    msg["Subject"] = f"Сводный отчёт аттестации — {datetime.now().strftime('%d.%m.%Y')}"

    body = f"""Добрый день!

Во вложении сводный отчёт по аттестации сотрудников.

Количество аттестаций: {count}
Дата формирования: {datetime.now().strftime('%d.%m.%Y %H:%M')}
{f'Период: {date_range}' if date_range else ''}

Отчёт сформирован автоматически ботом аттестации MDLZ.
"""
    msg.attach(MIMEText(body, "plain", "utf-8"))

    filename = f"Сводный_отчёт_{datetime.now().strftime('%d%m%Y')}.xlsx"
    with open(excel_path, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f"attachment; filename={filename}")
    msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.send_message(msg)
