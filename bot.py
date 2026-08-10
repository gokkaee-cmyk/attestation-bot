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
from openai import AsyncOpenAI

from questions import QUESTIONS, get_flat_questions, get_cases
from report import generate_report
from database import save_attestation, get_all_attestations, get_count, clear_attestations
from consolidated_report import generate_consolidated_report

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

SELECT_POSITION, ENTER_NAME, CONFIRM_NAME, ANSWERING, CONFIRM_TRANSCRIPT, CASE_ANSWERING, CASE_CONFIRM = range(7)

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


async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    keyboard = [[pos] for pos in POSITIONS.keys()]
    await update.message.reply_text(
        "🔄 Аттестация сброшена. Начинаем заново!\n\nПожалуйста, выберите вашу должность:",
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
    context.user_data["case_answers"] = []
    context.user_data["current_question"] = 0
    context.user_data["current_case"] = 0
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
            transcription = await openai_client.audio.transcriptions.create(
                model="whisper-1",
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


async def handle_voice_case(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Расшифровываю ваш ответ...")
    voice = update.message.voice
    voice_file = await voice.get_file()
    audio_path = Path(f"/tmp/voice_{update.effective_user.id}_{voice.file_id}.ogg")
    await voice_file.download_to_drive(audio_path)
    try:
        with open(audio_path, "rb") as audio_file:
            transcription = await openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=("audio.ogg", audio_file, "audio/ogg"),
                language="ru",
            )
        transcript_text = transcription.text.strip()
    except Exception as e:
        logger.error(f"Whisper error: {e}")
        await update.message.reply_text("❌ Не удалось расшифровать аудио. Попробуйте ещё раз.")
        return CASE_ANSWERING
    finally:
        audio_path.unlink(missing_ok=True)
    context.user_data["pending_case_transcript"] = transcript_text
    keyboard = [["✅ Верно", "🔄 Перезаписать"]]
    await update.message.reply_text(
        f"📝 *Расшифровка вашего ответа:*\n\n_{transcript_text}_\n\nВсё верно?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return CASE_CONFIRM


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
        logger.error(f"Evaluation error after all retries: {e}")
        await update.message.reply_text(
            "⚠️ Произошла техническая ошибка при оценке ответа.\n\n"
            "🎤 Пожалуйста, повторите ваш ответ голосовым сообщением ещё раз.",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data["pending_transcript"] = transcript_text
        return CONFIRM_TRANSCRIPT

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
        return await start_cases(update, context)
    await asyncio.sleep(1)
    await send_next_question(update, context)
    return ANSWERING


async def start_cases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    position_key = context.user_data["position_key"]
    cases = get_cases(position_key)

    if not cases:
        return await finish_attestation(update, context)

    context.user_data["cases"] = cases
    context.user_data["current_case"] = 0

    await update.message.reply_text(
        f"✅ Основные вопросы завершены!\n\n📌 Теперь разберём *{len(cases)} практических кейса*.\n\nВы получите обратную связь сразу после каждого ответа. Отвечайте голосовым сообщением.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    await asyncio.sleep(2)
    await send_next_case(update, context)
    return CASE_ANSWERING


async def send_next_case(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cases = context.user_data["cases"]
    idx = context.user_data["current_case"]
    total = len(cases)
    case = cases[idx]
    await update.message.reply_text(
        f"📌 Кейс {idx + 1} из {total}\n\n*{case['title']}*\n\n{case['case']}\n\n🎤 Ответьте голосовым сообщением.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )


async def confirm_case_transcript(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔄 Перезаписать":
        cases = context.user_data["cases"]
        idx = context.user_data["current_case"]
        await update.message.reply_text(
            f"🎤 Повторите ответ на кейс:\n\n*{cases[idx]['title']}*",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return CASE_ANSWERING

    transcript_text = context.user_data.pop("pending_case_transcript")
    cases = context.user_data["cases"]
    idx = context.user_data["current_case"]
    case = cases[idx]

    await update.message.reply_text("🤖 Анализирую ваш ответ на кейс...", reply_markup=ReplyKeyboardRemove())

    try:
        evaluation = await evaluate_case(
            case_title=case["title"],
            case_text=case["case"],
            reference_answer=case["reference_answer"],
            answer=transcript_text,
            position=context.user_data["position_name"],
        )
    except Exception as e:
        logger.error(f"Case evaluation error: {e}")
        await update.message.reply_text(
            "⚠️ Произошла техническая ошибка при оценке кейса.\n\n"
            "🎤 Пожалуйста, повторите ваш ответ голосовым сообщением ещё раз.",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data["pending_case_transcript"] = transcript_text
        return CASE_CONFIRM

    context.user_data["case_answers"].append({
        "title": case["title"],
        "case": case["case"],
        "transcript": transcript_text,
        "score": evaluation["score"],
        "strengths": evaluation.get("strengths", ""),
        "zones": evaluation.get("zones", ""),
        "feedback": evaluation.get("feedback", ""),
    })

    score = evaluation["score"]
    strengths = evaluation.get("strengths", "")
    zones = evaluation.get("zones", "")
    feedback = evaluation.get("feedback", "")

    feedback_text = f"💬 *Обратная связь по кейсу {idx + 1}: {score}%*\n\n"
    if strengths:
        feedback_text += f"✅ *Что получилось хорошо:*\n{strengths}\n\n"
    if zones:
        feedback_text += f"📈 *Зоны роста:*\n{zones}\n\n"
    if feedback:
        feedback_text += f"💡 *Рекомендация:*\n{feedback}"

    await update.message.reply_text(feedback_text, parse_mode="Markdown")

    context.user_data["current_case"] += 1
    total = len(cases)

    if context.user_data["current_case"] >= total:
        await asyncio.sleep(2)
        return await finish_attestation(update, context)

    await asyncio.sleep(2)
    await send_next_case(update, context)
    return CASE_ANSWERING


async def evaluate_answer(question: str, competency: str, answer: str, position: str) -> dict:
    prompt = f"""Ты — эксперт по оценке персонала компании Mondelez (МДЛЗ) в сфере FMCG/торговли.
Оцени ответ сотрудника на аттестационный вопрос.

Должность: {position}
Компетенция: {competency}
Вопрос: {question}
Ответ сотрудника: {answer}

ВАЖНЫЕ ПРАВИЛА ОЦЕНКИ:
- Оценивай ТОЛЬКО то, что спрашивается в вопросе — не добавляй свои критерии
- Если вопрос просит перечислить стандарты — оценивай только полноту перечисления, не требуй визуального мерчандайзинга или других тем которых нет в вопросе
- Если вопрос просит объяснить концепцию — оценивай понимание концепции, не требуй примеров если они не просились
- НЕ снижай балл за то, о чём вопрос не спрашивал
- Оценивай СУТЬ ответа, а не его длину
- Если сотрудник правильно отвечает на то что спросили — это хороший ответ (75-85%)
- Балл ниже 60 только если сотрудник явно не знает тему или отвечает не по существу вопроса
- НЕ требуй академической полноты — это практическая аттестация

Верни результат строго в формате JSON (без markdown, только чистый JSON):
{{
  "score": <число от 0 до 100>,
  "strengths": "<что сотрудник знает и понимает хорошо по данному вопросу, 1-2 предложения>",
  "weaknesses": "<чего не хватает именно в ответе на этот вопрос, 1-2 предложения, или пустая строка если ответ хороший>",
  "recommendation": "<конкретная рекомендация по данной теме, 1-2 предложения>"
}}

Критерии: 90-100 отличный, 75-89 хороший, 60-74 средний, 40-59 слабый, 0-39 неудовлетворительный."""

    last_error = None
    for attempt in range(3):
        try:
            response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                timeout=30,
            )
            raw = response.choices[0].message.content.strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            return json.loads(raw)
        except Exception as e:
            last_error = e
            logger.warning(f"Attempt {attempt + 1}/3 failed: {e}")
            if attempt < 2:
                await asyncio.sleep(3)

    raise last_error


async def evaluate_case(case_title: str, case_text: str, reference_answer: str, answer: str, position: str) -> dict:
    prompt = f"""Ты — эксперт по оценке персонала компании Mondelez (МДЛЗ) в сфере FMCG/торговли.
Оцени ответ сотрудника на практический кейс. Эталонный ответ = 100%.

Должность: {position}
Название кейса: {case_title}
Кейс: {case_text}
Эталонный ответ (100%): {reference_answer}
Ответ сотрудника: {answer}

ПРАВИЛА ОЦЕНКИ:
- Эталонный ответ — это 100%. Оценивай насколько ответ сотрудника приближен к эталону
- Если ответ пустой, бессмысленный или не относится к кейсу — ставь 0-10%
- Если сотрудник уловил только общую идею без деталей — 20-40%
- Если раскрыл основные моменты, но не все — 50-70%
- Если ответ близок к эталону по содержанию и логике — 75-90%
- 90-100% только если ответ полный и точный как эталон
- НЕ завышай оценку — будь честным и объективным

Верни результат строго в формате JSON (без markdown, только чистый JSON):
{{
  "score": <число от 0 до 100>,
  "strengths": "<что сотрудник сделал правильно относительно эталона, 2-3 предложения>",
  "zones": "<чего не хватило по сравнению с эталоном, 2-3 предложения>",
  "feedback": "<развивающая рекомендация как улучшить подход, 2-3 предложения>"
}}"""

    last_error = None
    for attempt in range(3):
        try:
            response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                timeout=30,
            )
            raw = response.choices[0].message.content.strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            return json.loads(raw)
        except Exception as e:
            last_error = e
            logger.warning(f"Case attempt {attempt + 1}/3 failed: {e}")
            if attempt < 2:
                await asyncio.sleep(3)

    raise last_error


async def finish_attestation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = context.user_data["name"]
    position_name = context.user_data["position_name"]
    position_key = context.user_data["position_key"]
    answers = context.user_data["answers"]
    case_answers = context.user_data.get("case_answers", [])
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
        "case_answers": case_answers,
    })

    Path(report_path).unlink(missing_ok=True)
    context.user_data.clear()
    return ConversationHandler.END


async def send_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ У вас нет доступа к этой команде.")
        return

    args = context.args
    show_all = args and args[0].lower() == "all"

    all_attestations = get_all_attestations()
    count_total = len(all_attestations)

    if count_total == 0:
        await update.message.reply_text("📭 Нет сохранённых аттестаций.")
        return

    if show_all:
        attestations = all_attestations
    else:
        attestations = [a for a in all_attestations if a.get("overall_avg", 0) >= 80]

    count_passed = len([a for a in all_attestations if a.get("overall_avg", 0) >= 80])
    count_failed = count_total - count_passed

    if len(attestations) == 0:
        await update.message.reply_text(
            f"📭 Никто ещё не сдал аттестацию.\n\n👥 Всего прошли: {count_total}\n✅ Сдали: 0\n❌ Не сдали: {count_total}"
        )
        return

    label = "всем сотрудникам" if show_all else "сдавшим аттестацию"
    await update.message.reply_text(f"⏳ Формирую отчёт по {label}...")

    try:
        report_path = await generate_consolidated_report(attestations)
        filename = f"{'Все' if show_all else 'Сдавшие'}_{datetime.now().strftime('%d%m%Y_%H%M')}.xlsx"
        with open(report_path, "rb") as f:
            await context.bot.send_document(
                chat_id=ADMIN_CHAT_ID, document=f, filename=filename,
                caption=(
                    f"📊 *{'Полный отчёт' if show_all else 'Сдавшие аттестацию'}*\n\n"
                    f"👥 Всего прошли: {count_total}\n"
                    f"✅ Сдали: {count_passed}\n"
                    f"❌ Не сдали: {count_failed}"
                ),
                parse_mode="Markdown",
            )
        Path(report_path).unlink(missing_ok=True)
        await update.message.reply_text(
            f"✅ Готово!\n\n/report — только сдавшие\n/report all — все сотрудники\n/clear — очистить базу"
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
    all_attestations = get_all_attestations()
    count_total = len(all_attestations)
    count_passed = len([a for a in all_attestations if a.get("overall_avg", 0) >= 80])
    count_failed = count_total - count_passed
    await update.message.reply_text(
        f"📊 *Статус аттестации*\n\n"
        f"👥 Всего прошли: *{count_total}*\n"
        f"✅ Сдали: *{count_passed}*\n"
        f"❌ Не сдали: *{count_failed}*\n\n"
        f"/report — отчёт по сдавшим\n"
        f"/report all — отчёт по всем\n"
        f"/clear — очистить базу",
        parse_mode="Markdown",
    )


async def handle_text_during_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎤 Пожалуйста, отвечайте *только голосовым сообщением*.", parse_mode="Markdown")
    return ANSWERING


async def handle_text_during_case(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎤 Пожалуйста, отвечайте *только голосовым сообщением*.", parse_mode="Markdown")
    return CASE_ANSWERING


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Аттестация отменена. Для начала нажмите /start", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("restart", restart)],
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
            CASE_ANSWERING: [
                MessageHandler(filters.VOICE, handle_voice_case),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_during_case),
            ],
            CASE_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_case_transcript),
                MessageHandler(filters.VOICE, handle_voice_case),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("restart", restart)],
    )
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("report", send_report))
    app.add_handler(CommandHandler("clear", clear_db))
    app.add_handler(CommandHandler("status", status))
    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
