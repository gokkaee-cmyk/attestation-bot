import asyncio
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


def _border(style="thin"):
    s = Side(style=style)
    return Border(left=s, right=s, top=s, bottom=s)

def _fill(hex_color):
    return PatternFill("solid", fgColor=hex_color)


async def generate_consolidated_report(attestations: list) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _build_consolidated, attestations)


def _build_consolidated(attestations: list) -> str:
    wb = Workbook()

    ws1 = wb.active
    ws1.title = "Сводная таблица"

    ws1.merge_cells("A1:E1")
    c = ws1["A1"]
    c.value = f"СВОДНЫЙ ОТЧЁТ АТТЕСТАЦИИ — {datetime.now().strftime('%d.%m.%Y')}"
    c.font = Font(name="Arial", bold=True, size=13, color="FFFFFF")
    c.fill = _fill("1F3864")
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[1].height = 32

    headers = ["ФИО", "Должность", "Средний %", "Статус", "Дата аттестации"]
    col_widths = [32, 35, 14, 30, 20]
    for col_idx, (h, w) in enumerate(zip(headers, col_widths), start=1):
        cell = ws1.cell(row=2, column=col_idx, value=h)
        cell.font = Font(name="Arial", bold=True, size=11, color="FFFFFF")
        cell.fill = _fill("2E75B6")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _border()
        ws1.column_dimensions[get_column_letter(col_idx)].width = w
    ws1.row_dimensions[2].height = 28
    ws1.freeze_panes = "A3"

    for row_idx, att in enumerate(attestations, start=3):
        overall = round(att.get("overall_avg", 0))
        verdict = att.get("verdict", "")
        date_str = ""
        try:
            date_str = datetime.fromisoformat(att["start_time"]).strftime("%d.%m.%Y %H:%M")
        except:
            date_str = att.get("start_time", "")

        row_data = [
            att.get("name", ""),
            att.get("position_name", ""),
            f"{overall}%",
            verdict,
            date_str,
        ]

        fill_color = "EBF3FB" if row_idx % 2 == 0 else "FFFFFF"

        for col_idx, val in enumerate(row_data, start=1):
            cell = ws1.cell(row=row_idx, column=col_idx, value=val)
            cell.font = Font(name="Arial", size=10)
            cell.fill = _fill(fill_color)
            cell.alignment = Alignment(
                horizontal="center" if col_idx in (3, 4, 5) else "left",
                vertical="center", wrap_text=True,
                indent=0 if col_idx in (3, 4, 5) else 1
            )
            cell.border = _border()
            if col_idx in (3, 4):
                if overall >= 80:
                    cell.fill = _fill("C6EFCE")
                    cell.font = Font(name="Arial", size=10, bold=True, color="276221")
                else:
                    cell.fill = _fill("FFC7CE")
                    cell.font = Font(name="Arial", size=10, bold=True, color="9C0006")

        ws1.row_dimensions[row_idx].height = 22

    ws2 = wb.create_sheet("Детальные ответы")

    ws2.merge_cells("A1:G1")
    c2 = ws2["A1"]
    c2.value = "ДЕТАЛЬНЫЕ РЕЗУЛЬТАТЫ АТТЕСТАЦИИ"
    c2.font = Font(name="Arial", bold=True, size=13, color="FFFFFF")
    c2.fill = _fill("1F3864")
    c2.alignment = Alignment(horizontal="center", vertical="center")
    ws2.row_dimensions[1].height = 32

    headers2 = ["ФИО", "Должность", "Компетенция", "%", "Сильные стороны", "Зоны развития", "Рекомендации"]
    col_widths2 = [28, 32, 24, 8, 35, 35, 40]
    for col_idx, (h, w) in enumerate(zip(headers2, col_widths2), start=1):
        cell = ws2.cell(row=2, column=col_idx, value=h)
        cell.font = Font(name="Arial", bold=True, size=11, color="FFFFFF")
        cell.fill = _fill("2E75B6")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _border()
        ws2.column_dimensions[get_column_letter(col_idx)].width = w
    ws2.row_dimensions[2].height = 28
    ws2.freeze_panes = "A3"

    row = 3
    for att in attestations:
        name = att.get("name", "")
        position = att.get("position_name", "")
        competency_avg = att.get("competency_avg", {})

        first_person = True
        for comp, avg in competency_avg.items():
            avg_r = round(avg)
            row_data = [
                name if first_person else "",
                position if first_person else "",
                comp,
                f"{avg_r}%",
                "", "", "",
            ]
            for col_idx, val in enumerate(row_data, start=1):
                cell = ws2.cell(row=row, column=col_idx, value=val)
                cell.font = Font(name="Arial", size=10, bold=True)
                cell.fill = _fill("EBF3FB")
                cell.alignment = Alignment(
                    horizontal="center" if col_idx == 4 else "left",
                    vertical="center", wrap_text=True,
                    indent=0 if col_idx == 4 else 1
                )
                cell.border = _border()
                if col_idx == 4:
                    if avg_r >= 80:
                        cell.fill = _fill("C6EFCE")
                        cell.font = Font(name="Arial", size=10, bold=True, color="276221")
                    elif avg_r >= 60:
                        cell.fill = _fill("FFEB9C")
                        cell.font = Font(name="Arial", size=10, bold=True, color="9C6500")
                    else:
                        cell.fill = _fill("FFC7CE")
                        cell.font = Font(name="Arial", size=10, bold=True, color="9C0006")
            ws2.row_dimensions[row].height = 20
            row += 1
            first_person = False

        for col_idx in range(1, 8):
            cell = ws2.cell(row=row, column=col_idx, value="")
            cell.fill = _fill("D9E1F2")
            cell.border = _border()
        ws2.row_dimensions[row].height = 6
        row += 1

    output_path = f"/tmp/consolidated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    wb.save(output_path)
    return output_path
