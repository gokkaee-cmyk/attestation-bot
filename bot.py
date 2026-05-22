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

from questions import QUESTIONS
from report import generate_report

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

# Conversation states
SELECT_POSITION, ENTER_NAME, CONFIRM_NAME, ANSWERING, CONFIRM_TRANSCRIPT = range(5)

POSITIONS = [
    "Супервайзер",
    "Территориальный менеджер",
    "Стационарный мерчандайзер",
    "Торговый представитель",
]

POSITION_KEYS = {
    "Супервайзер": "supervisor",
    "Территориальный менеджер": "territory_manager",
    "Стационарный мерчандайзер": "stationary_merchandiser",
    "Торговый представитель": "sales_rep",
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    keyboard = [[pos] for pos in POSITIONS]
    await update.message.reply_text(
        "👋 Добро пожаловать на аттестацию!\n\n"
        "Пожалуйста, выберите вашу должность:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return SELECT_POSITION


async def select_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    position = update.message.text
    if position not in POSITIONS:
        await update.message.reply_text("Пожалуйста, выберите должность из списка.")
        return SELECT_POSITION

    context.user_data["position"] = position
    context.user_data["position_key"] = POSITION_KEYS[position]
    await update.message.reply_text(
        f"✅ Должность: *{position}*\n\nВведите ваше ФИО:",
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
        f"Проверьте данные:\n\n"
        f"👤 ФИО: *{name}*\n"
        f"💼 Должность: *{context.user_data['position']}*",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return CONFIRM_NAME


async def confirm_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "✏️ Изменить":
        await update.message.reply_text(
            "Введите ФИО заново:", reply_markup=ReplyKeyboardRemove()
        )
        return ENTER_NAME

    context.user_data["answers"] = []
    context.user_data["current_question"] = 0
    context.user_data["start_time"] = datetime.now().isoformat()

    await send_next_question(update, context)
    return ANSWERING


async def send_next_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pos_key = context.user_data["position_key"]
    questions = QUESTIONS[pos_key]
    idx = context.user_data["current_question"]
    total = len(questions)

    await update.message.reply_text(
        f"📋 Вопрос {idx + 1} из {total}:\n\n"
        f"*{questions[idx]}*\n\n"
        f"🎤 Ответьте голосовым сообщением.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Расшифровываю ваш ответ...")

    voice = update.message.voice
    voice_file = await voice.get_file()
    audio_path = Path(f"/tmp/voice_{update.effective_user.id}_{voice.file_id}.ogg")
    await voice_file.download_to_drive(audio_path)

    # Transcribe with Groq Whisper
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
        await update.message.reply_text(
            "❌ Не удалось расшифровать аудио. Попробуйте ещё раз."
        )
        return ANSWERING
    finally:
        audio_path.unlink(missing_ok=True)

    context.user_data["pending_transcript"] = transcript_text

    keyboard = [["✅ Верно", "🔄 Перезаписать"]]
    await update.message.reply_text(
        f"📝 *Расшифровка вашего ответа:*\n\n_{transcript_text}_\n\n"
        f"Всё верно?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return CONFIRM_TRANSCRIPT


async def confirm_transcript(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔄 Перезаписать":
        pos_key = context.user_data["position_key"]
        questions = QUESTIONS[pos_key]
        idx = context.user_data["current_question"]
        await update.message.reply_text(
            f"🎤 Повторите ответ на вопрос:\n\n*{questions[idx]}*",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ANSWERING

    transcript_text = context.user_data.pop("pending_transcript")
    pos_key = context.user_data["position_key"]
    questions = QUESTIONS[pos_key]
    idx = context.user_data["current_question"]
    question = questions[idx]

    await update.message.reply_text(
        "🤖 Оцениваю ответ...", reply_markup=ReplyKeyboardRemove()
    )

    try:
        evaluation = await evaluate_answer(
            question=question,
            answer=transcript_text,
            position=context.user_data["position"],
        )
    except Exception as e:
        logger.error(f"Evaluation error: {e}")
        evaluation = {"score": 0, "comment": "Ошибка оценки", "recommendation": ""}

    context.user_data["answers"].append({
        "question": question,
        "transcript": transcript_text,
        "score": evaluation["score"],
        "comment": evaluation["comment"],
        "recommendation": evaluation["recommendation"],
    })

    await update.message.reply_text(
        "✅ Ответ принят! Переходим к следующему вопросу.",
    )

    context.user_data["current_question"] += 1
    total = len(questions)

    if context.user_data["current_question"] >= total:
        return await finish_attestation(update, context)

    await asyncio.sleep(1)
    await send_next_question(update, context)
    return ANSWERING


async def evaluate_answer(question: str, answer: str, position: str) -> dict:
    prompt = f"""Ты — эксперт по оценке персонала в сфере FMCG/торговли.
Оцени ответ сотрудника на аттестационный вопрос.

Должность: {position}
Вопрос: {question}
Ответ сотрудника: {answer}

Дай оценку строго в формате JSON (без markdown-блоков, только чистый JSON):
{{
  "score": <число от 1 до 10>,
  "comment": "<краткий комментарий к ответу, 1-2 предложения>",
  "recommendation": "<рекомендация по развитию, 1-2 предложения>"
}}

Критерии оценки:
- 9-10: Полный, развёрнутый, профессиональный ответ
- 7-8: Хороший ответ с небольшими пробелами
- 5-6: Средний ответ, основные моменты раскрыты частично
- 3-4: Слабый ответ, поверхностное понимание
- 1-2: Ответ не по теме или отсутствует понимание"""

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
    position = context.user_data["position"]
    answers = context.user_data["answers"]
    start_time = context.user_data["start_time"]

    scores = [a["score"] for a in answers]
    avg_score = sum(scores) / len(scores) if scores else 0

    if avg_score >= 8:
        verdict = "АТТЕСТОВАН(А)"
        verdict_emoji = "🟢"
    elif avg_score >= 6:
        verdict = "УСЛОВНО АТТЕСТОВАН(А)"
        verdict_emoji = "🟡"
    else:
        verdict = "НЕ АТТЕСТОВАН(А)"
        verdict_emoji = "🔴"

    await update.message.reply_text(
        f"🎉 *Аттестация завершена!*\n\n"
        f"👤 {name}\n"
        f"💼 {position}\n\n"
        f"Все ваши ответы записаны. Результаты будут направлены руководителю.\n\n"
        f"Спасибо за участие!",
        parse_mode="Markdown",
    )

    report_path = await generate_report(
        name=name,
        position=position,
        answers=answers,
        start_time=start_time,
        avg_score=avg_score,
        verdict=verdict,
    )

    with open(report_path, "rb") as f:
        await context.bot.send_document(
            chat_id=ADMIN_CHAT_ID,
            document=f,
            filename=f"attestation_{name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            caption=(
                f"📋 *Новый отчёт аттестации*\n\n"
                f"👤 {name}\n"
                f"💼 {position}\n"
                f"⭐ Средняя оценка: {avg_score:.1f}/10\n"
                f"{verdict_emoji} {verdict}"
            ),
            parse_mode="Markdown",
        )

    Path(report_path).unlink(missing_ok=True)
    context.user_data.clear()
    return ConversationHandler.END


async def handle_text_during_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎤 Пожалуйста, отвечайте *только голосовым сообщением*.",
        parse_mode="Markdown",
    )
    return ANSWERING


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Аттестация отменена. Для начала нажмите /start",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_POSITION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, select_position)
            ],
            ENTER_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name)
            ],
            CONFIRM_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_name)
            ],
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
    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
