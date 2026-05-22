import asyncio
from datetime import datetime
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side, GradientFill
)
from openpyxl.utils import get_column_letter


def _make_border(style="thin"):
    s = Side(style=style)
    return Border(left=s, right=s, top=s, bottom=s)


def _header_fill(hex_color: str):
    return PatternFill("solid", fgColor=hex_color)


async def generate_report(
    name: str,
    position: str,
    answers: list,
    start_time: str,
    avg_score: float,
    verdict: str,
) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _build_excel, name, position, answers, start_time, avg_score, verdict
    )


def _build_excel(name, position, answers, start_time, avg_score, verdict):
    wb = Workbook()

    # ── Sheet 1: Summary ──────────────────────────────────────────────────────
    ws_summary = wb.active
    ws_summary.title = "Итог"

    # Title
    ws_summary.merge_cells("A1:F1")
    title_cell = ws_summary["A1"]
    title_cell.value = "ОТЧЁТ ПО АТТЕСТАЦИИ СОТРУДНИКА"
    title_cell.font = Font(name="Arial", bold=True, size=14, color="FFFFFF")
    title_cell.fill = _header_fill("1F3864")
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws_summary.row_dimensions[1].height = 36

    # Info block
    info_rows = [
        ("ФИО сотрудника", name),
        ("Должность", position),
        ("Дата аттестации", datetime.fromisoformat(start_time).strftime("%d.%m.%Y %H:%M")),
        ("Количество вопросов", str(len(answers))),
        ("Средняя оценка", f"{avg_score:.1f} / 10"),
        ("Результат", verdict.replace("✅", "").replace("⚠️", "").replace("❌", "").strip()),
    ]

    label_fill = _header_fill("D9E1F2")
    value_fill = _header_fill("EBF3FB")

    for i, (label, value) in enumerate(info_rows, start=2):
        lc = ws_summary.cell(row=i, column=1, value=label)
        lc.font = Font(name="Arial", bold=True, size=11)
        lc.fill = label_fill
        lc.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        lc.border = _make_border()

        ws_summary.merge_cells(f"B{i}:F{i}")
        vc = ws_summary.cell(row=i, column=2, value=value)
        vc.font = Font(name="Arial", size=11)
        vc.fill = value_fill
        vc.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        vc.border = _make_border()
        ws_summary.row_dimensions[i].height = 22

    # Score colour indicator
    score_row = 6  # "Средняя оценка" is row 6
    score_cell = ws_summary.cell(row=score_row, column=2)
    if avg_score >= 8:
        score_cell.fill = _header_fill("C6EFCE")
        score_cell.font = Font(name="Arial", size=11, bold=True, color="276221")
    elif avg_score >= 6:
        score_cell.fill = _header_fill("FFEB9C")
        score_cell.font = Font(name="Arial", size=11, bold=True, color="9C6500")
    else:
        score_cell.fill = _header_fill("FFC7CE")
        score_cell.font = Font(name="Arial", size=11, bold=True, color="9C0006")

    # Column widths summary sheet
    ws_summary.column_dimensions["A"].width = 26
    for col in ["B", "C", "D", "E", "F"]:
        ws_summary.column_dimensions[col].width = 22

    # ── Sheet 2: Detailed answers ─────────────────────────────────────────────
    ws_detail = wb.create_sheet("Подробные ответы")

    # Header row
    headers = ["№", "Вопрос", "Ответ сотрудника (расшифровка)", "Оценка (1-10)", "Комментарий эксперта", "Рекомендация по развитию"]
    col_widths = [5, 40, 45, 14, 40, 45]

    for col_idx, (header, width) in enumerate(zip(headers, col_widths), start=1):
        cell = ws_detail.cell(row=1, column=col_idx, value=header)
        cell.font = Font(name="Arial", bold=True, size=11, color="FFFFFF")
        cell.fill = _header_fill("1F3864")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _make_border()
        ws_detail.column_dimensions[get_column_letter(col_idx)].width = width

    ws_detail.row_dimensions[1].height = 36
    ws_detail.freeze_panes = "A2"

    # Data rows
    alt_fill = _header_fill("EBF3FB")
    white_fill = _header_fill("FFFFFF")

    for row_idx, answer in enumerate(answers, start=2):
        row_data = [
            row_idx - 1,
            answer["question"],
            answer["transcript"],
            answer["score"],
            answer["comment"],
            answer["recommendation"],
        ]
        fill = alt_fill if row_idx % 2 == 0 else white_fill

        for col_idx, value in enumerate(row_data, start=1):
            cell = ws_detail.cell(row=row_idx, column=col_idx, value=value)
            cell.font = Font(name="Arial", size=10)
            cell.fill = fill
            cell.alignment = Alignment(
                horizontal="center" if col_idx in (1, 4) else "left",
                vertical="top",
                wrap_text=True,
                indent=0 if col_idx in (1, 4) else 1,
            )
            cell.border = _make_border()

            # Score colour coding
            if col_idx == 4:
                score = answer["score"]
                if score >= 8:
                    cell.fill = _header_fill("C6EFCE")
                    cell.font = Font(name="Arial", size=10, bold=True, color="276221")
                elif score >= 6:
                    cell.fill = _header_fill("FFEB9C")
                    cell.font = Font(name="Arial", size=10, bold=True, color="9C6500")
                else:
                    cell.fill = _header_fill("FFC7CE")
                    cell.font = Font(name="Arial", size=10, bold=True, color="9C0006")

        ws_detail.row_dimensions[row_idx].height = 80

    # ── Sheet 3: Score chart data ─────────────────────────────────────────────
    ws_chart = wb.create_sheet("Оценки по вопросам")

    chart_headers = ["Вопрос №", "Оценка", "Максимум"]
    for col_idx, h in enumerate(chart_headers, start=1):
        cell = ws_chart.cell(row=1, column=col_idx, value=h)
        cell.font = Font(name="Arial", bold=True, size=11, color="FFFFFF")
        cell.fill = _header_fill("1F3864")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = _make_border()

    for i, answer in enumerate(answers, start=1):
        ws_chart.cell(row=i + 1, column=1, value=f"Вопрос {i}").border = _make_border()
        score_cell = ws_chart.cell(row=i + 1, column=2, value=answer["score"])
        score_cell.border = _make_border()
        max_cell = ws_chart.cell(row=i + 1, column=3, value=10)
        max_cell.border = _make_border()

    # Average row
    last_data_row = len(answers) + 2
    ws_chart.cell(row=last_data_row, column=1, value="СРЕДНЕЕ").font = Font(name="Arial", bold=True)
    avg_cell = ws_chart.cell(row=last_data_row, column=2, value=f"=AVERAGE(B2:B{len(answers)+1})")
    avg_cell.font = Font(name="Arial", bold=True)
    avg_cell.number_format = "0.0"
    avg_cell.border = _make_border()

    for col in ["A", "B", "C"]:
        ws_chart.column_dimensions[col].width = 20

    # Save
    output_path = f"/tmp/attestation_{name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    wb.save(output_path)
    return output_path
