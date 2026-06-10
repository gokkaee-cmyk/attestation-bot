import os
import json
import logging
import asyncio
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
from groq import AsyncGroq

from questions import QUESTIONS, get_flat_questions
from report import generate_report
from database import save_attestation, get_all_attestations, get_count, clear_attestations
from consolidated_report import generate_consolidated_report
from email_sender import send_report_email

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))

groq_client = AsyncGroq(api_key=GROQ_API_KEY)

SELECT_POSITION, ENTER_NAME, CONFIRM_NAME, ANSWERING, CONFIRM_TRANSCRIPT = range(5)

POSITIONS = {
    "ЭТА (Торговый агент)": "eta",
    "СМР (Стационарный мерчандайзер)": "smr",
    "ТС ТТ (Торговый супервайзер ТТ)": "ts_tt",
    "ТМ ТТ (Территориальный менеджер ТТ)": "tm_tt",
    "ТС МТ (Торговый супервайзер МТ)": "ts_mt",
    "ТМ МТ (Территориальный менеджер МТ)": "tm_mt",
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    keyboard = [[pos] for pos in POSITIONS.keys()]
    await update.message.reply_text(
        "👋 Добро пожаловать на аттестацию MDLZ!\n\nПожалуйста, выберите вашу должность:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return SELECT_POSITION


async def select_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    position_name = update.message.text
    if position_name not in POSITIONS:
        await update.message.reply_text("Пожалуйста, выберите должность из списка.")
        return SELECT_POSITION
    position_key = POSITIONS[position_name]
    context.user_data["position_name"] = position_name
    context.user_data["position_key"] = position_key
    await update.message.reply_text(
        f"✅ Должность: *{position_name}*\n\nВведите ваше ФИО:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ENTER_NAME


async def enter_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if len(name) < 3:
        await update.message.reply_text("Пожалуйста, введите полное ФИО.")
        return ENTER_NAME
    context.user_data["name"] = name
    keyboard = [["✅ Верно", "✏️ Изменить"]]
    await update.message.reply_text(
        f"Проверьте данные:\n\n👤 ФИО: *{name}*\n💼 Должность: *{context.user_data['position_name']}*",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return CONFIRM_NAME


async def confirm_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "✏️ Изменить":
        await update.message.reply_text("Введите ФИО заново:", reply_markup=ReplyKeyboardRemove())
        return ENTER_NAME
    context.user_data["answers"] = []
    context.user_data["current_question"] = 0
    context.user_data["start_time"] = datetime.now().isoformat()
    flat = get_flat_questions(context.user_data["position_key"])
    context.user_data["flat_questions"] = flat
    total = len(flat)
    await update.message.reply_text(
        f"📋 Аттестация начинается!\nВсего вопросов: *{total}*\n\nОтвечайте только голосовым сообщением. Удачи! 💪",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    await asyncio.sleep(1)
    await send_next_question(update, context)
    return ANSWERING


async def send_next_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flat = context.user_data["flat_questions"]
    idx = context.user_data["current_question"]
    total = len(flat)
    item = flat[idx]
    await update.message.reply_text(
        f"📋 Вопрос {idx + 1} из {total}\n🏷 Компетенция: *{item['competency']}*\n\n*{item['question']}*\n\n🎤 Ответьте голосовым сообщением.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Расшифровываю ваш ответ...")
    voice = update.message.voice
    voice_file = await voice.get_file()
    audio_path = Path(f"/tmp/voice_{update.effective_user.id}_{voice.file_id}.ogg")
    await voice_file.download_to_drive(audio_path)
    try:
        with open(audio_path, "rb") as audio_file:
            transcription = await groq_client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=("audio.ogg", audio_file, "audio/ogg"),
                language="ru",
            )
        transcript_text = transcription.text.strip()
    except Exception as e:
        logger.error(f"Whisper error: {e}")
        await update.message.reply_text("❌ Не удалось расшифровать аудио. Попробуйте ещё раз.")
        return ANSWERING
    finally:
        audio_path.unlink(missing_ok=True)
    context.user_data["pending_transcript"] = transcript_text
    keyboard = [["✅ Верно", "🔄 Перезаписать"]]
    await update.message.reply_text(
        f"📝 *Расшифровка вашего ответа:*\n\n_{transcript_text}_\n\nВсё верно?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return CONFIRM_TRANSCRIPT


async def confirm_transcript(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔄 Перезаписать":
        flat = context.user_data["flat_questions"]
        idx = context.user_data["current_question"]
        await update.message.reply_text(
            f"🎤 Повторите ответ на вопрос:\n\n*{flat[idx]['question']}*",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ANSWERING
    transcript_text = context.user_data.pop("pending_transcript")
    flat = context.user_data["flat_questions"]
    idx = context.user_data["current_question"]
    item = flat[idx]
    await update.message.reply_text("🤖 Оцениваю ответ...", reply_markup=ReplyKeyboardRemove())
    try:
        evaluation = await evaluate_answer(
            question=item["question"],
            competency=item["competency"],
            answer=transcript_text,
            position=context.user_data["position_name"],
        )
    except Exception as e:
        logger.error(f"Evaluation error: {e}")
        evaluation = {"score": 0, "strengths": "", "weaknesses": "", "recommendation": "Ошибка оценки"}
    context.user_data["answers"].append({
        "competency": item["competency"],
        "question": item["question"],
        "transcript": transcript_text,
        "score": evaluation["score"],
        "strengths": evaluation.get("strengths", ""),
        "weaknesses": evaluation.get("weaknesses", ""),
        "recommendation": evaluation["recommendation"],
    })
    await update.message.reply_text("✅ Ответ принят! Переходим к следующему вопросу.")
    context.user_data["current_question"] += 1
    total = len(flat)
    if context.user_data["current_question"] >= total:
        return await finish_attestation(update, context)
    await asyncio.sleep(1)
    await send_next_question(update, context)
    return ANSWERING


async def evaluate_answer(question: str, competency: str, answer: str, position: str) -> dict:
    prompt = f"""Ты — эксперт по оценке персонала компании Mondelez (МДЛЗ) в сфере FMCG/торговли.
Оцени ответ сотрудника на аттестационный вопрос.

Должность: {position}
Компетенция: {competency}
Вопрос: {question}
Ответ сотрудника: {answer}

Верни результат строго в формате JSON (без markdown, только чистый JSON):
{{
  "score": <число от 0 до 100>,
  "strengths": "<сильные стороны ответа, 1-2 предложения>",
  "weaknesses": "<зоны развития, 1-2 предложения, или пустая строка если ответ отличный>",
  "recommendation": "<конкретная рекомендация по улучшению, 1-2 предложения>"
}}

Критерии: 90-100 отличный, 75-89 хороший, 60-74 средний, 40-59 слабый, 0-39 неудовлетворительный.
Если вопрос не требует примера из жизни — не снижай балл за его отсутствие."""

    response = await groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    raw = response.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


async def finish_attestation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = context.user_data["name"]
    position_name = context.user_data["position_name"]
    position_key = context.user_data["position_key"]
    answers = context.user_data["answers"]
    start_time = context.user_data["start_time"]

    competency_scores = {}
    for a in answers:
        comp = a["competency"]
        if comp not in competency_scores:
            competency_scores[comp] = []
        competency_scores[comp].append(a["score"])

    competency_avg = {c: sum(s)/len(s) for c, s in competency_scores.items()}
    overall_avg = sum(competency_avg.values()) / len(competency_avg) if competency_avg else 0

    if overall_avg >= 80:
        verdict = "УСПЕШНО СДАЛ(А) АТТЕСТАЦИЮ"
        verdict_emoji = "🟢"
    else:
        verdict = "НЕ СДАЛ(А) АТТЕСТАЦИЮ"
        verdict_emoji = "🔴"

    await update.message.reply_text(
        f"🎉 *Аттестация завершена!*\n\n👤 {name}\n💼 {position_name}\n\nВсе ваши ответы записаны. Результаты будут направлены руководителю.\n\nСпасибо за участие!",
        parse_mode="Markdown",
    )

    report_path = await generate_report(
        name=name, position_name=position_name, position_key=position_key,
        answers=answers, competency_avg=competency_avg,
        overall_avg=overall_avg, verdict=verdict, start_time=start_time,
    )

    filename = f"Аттестация_{name.replace(' ', '_')}_{datetime.now().strftime('%d%m%Y_%H%M')}.xlsx"
    with open(report_path, "rb") as f:
        await context.bot.send_document(
            chat_id=ADMIN_CHAT_ID, document=f, filename=filename,
            caption=(
                f"📋 *Новый отчёт аттестации*\n\n👤 {name}\n💼 {position_name}\n"
                f"📊 Средний %: {overall_avg:.0f}%\n{verdict_emoji} {verdict}"
            ),
            parse_mode="Markdown",
        )

    save_attestation({
        "name": name, "position_name": position_name, "position_key": position_key,
        "answers": answers, "competency_avg": competency_avg,
        "overall_avg": overall_avg, "verdict": verdict, "start_time": start_time,
    })

    Path(report_path).unlink(missing_ok=True)
    context.user_data.clear()
    return ConversationHandler.END


async def send_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ У вас нет доступа к этой команде.")
        return
    count = get_count()
    if count == 0:
        await update.message.reply_text("📭 Нет сохранённых аттестаций.")
        return
    await update.message.reply_text(f"⏳ Формирую сводный отчёт по {count} аттестациям...")
    try:
        attestations = get_all_attestations()
        report_path = await generate_consolidated_report(attestations)
        send_report_email(report_path, count)
        filename = f"Сводный_отчёт_{datetime.now().strftime('%d%m%Y_%H%M')}.xlsx"
        with open(report_path, "rb") as f:
            await context.bot.send_document(
                chat_id=ADMIN_CHAT_ID, document=f, filename=filename,
                caption=(
                    f"📊 *Сводный отчёт аттестации*\n\n👥 Сотрудников: {count}\n"
                    f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                    f"📧 Также отправлен на tamaeva27@gmail.com"
                ),
                parse_mode="Markdown",
            )
        Path(report_path).unlink(missing_ok=True)
        await update.message.reply_text(
            f"✅ Отчёт по {count} аттестациям отправлен!\n📧 Email: tamaeva27@gmail.com\n\nДля очистки базы: /clear"
        )
    except Exception as e:
        logger.error(f"Report error: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def clear_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ У вас нет доступа к этой команде.")
        return
    count = get_count()
    clear_attestations()
    await update.message.reply_text(f"🗑 База очищена. Удалено аттестаций: {count}")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ У вас нет доступа к этой команде.")
        return
    count = get_count()
    await update.message.reply_text(
        f"📊 *Статус базы*\n\n👥 Накоплено аттестаций: *{count}*\n\n/report — сводный отчёт на email\n/clear — очистить базу",
        parse_mode="Markdown",
    )


async def handle_text_during_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎤 Пожалуйста, отвечайте *только голосовым сообщением*.", parse_mode="Markdown")
    return ANSWERING


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Аттестация отменена. Для начала нажмите /start", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_POSITION: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_position)],
            ENTER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name)],
            CONFIRM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_name)],
            ANSWERING: [
                MessageHandler(filters.VOICE, handle_voice),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_during_voice),
            ],
            CONFIRM_TRANSCRIPT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_transcript),
                MessageHandler(filters.VOICE, handle_voice),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("report", send_report))
    app.add_handler(CommandHandler("clear", clear_db))
    app.add_handler(CommandHandler("status", status))
    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
