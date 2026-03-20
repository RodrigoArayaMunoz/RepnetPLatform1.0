from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font


class PublicationsWithoutCompatibilityService:
    def __init__(self, repository):
        self.repository = repository

    def get_paginated(self, page: int, page_size: int, q: str | None = None):
        return self.repository.get_without_compatibilities_paginated(
            page=page,
            page_size=page_size,
            q=q,
        )

    def get_all_for_export(self, q: str | None = None):
        return self.repository.get_without_compatibilities_for_export(q=q)

    def build_excel_file(self, q: str | None = None) -> BytesIO:
        items = self.get_all_for_export(q=q)

        workbook = Workbook(write_only=False)
        worksheet = workbook.active
        worksheet.title = "Sin compatibilidades"

        worksheet.append(["MLC", "Título"])

        header_font = Font(bold=True)
        worksheet["A1"].font = header_font
        worksheet["B1"].font = header_font

        for item in items:
            worksheet.append([
                item.get("mlc") or "-",
                item.get("title") or "-",
            ])

        worksheet.column_dimensions["A"].width = 22
        worksheet.column_dimensions["B"].width = 90

        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return output