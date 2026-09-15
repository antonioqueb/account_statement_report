"""Small dependency-free tabular writers; Excel cells are literals, never formulas."""
import csv
from decimal import Decimal
from io import BytesIO, StringIO
import re
import zipfile
from xml.sax.saxutils import escape


def safe_cell(value):
    if value is None or value is False:
        return ''
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
        return "'" + value
    return value


def csv_bytes(headers, rows):
    stream = StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows([[safe_cell(v) for v in row] for row in rows])
    return ('\ufeff' + stream.getvalue()).encode('utf-8')


def xlsx_bytes(sheets):
    """OOXML with inline literal strings; decimal text retains source precision."""
    output = BytesIO()
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        types = ['<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>']
        workbook, rels = [], []
        for index, (name, headers, rows) in enumerate(sheets, 1):
            types.append('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % index)
            workbook.append('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(name, {'"': '&quot;'}), index, index))
            rels.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (index, index))
            with archive.open('xl/worksheets/sheet%d.xml' % index, 'w') as stream:
                stream.write(('<worksheet xmlns="%s"><sheetData>' % ns).encode())
                for row in [headers] + rows:
                    cells = []
                    for value in row:
                        if type(value) in (int, float, Decimal):
                            cells.append('<c t="n"><v>%s</v></c>' % value)
                        else:
                            literal = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(safe_cell(value)))
                            cells.append('<c t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % escape(literal))
                    cells = ''.join(cells)
                    stream.write(('<row>' + cells + '</row>').encode('utf-8'))
                stream.write(b'</sheetData></worksheet>')
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>' + ''.join(types) + '</Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', '<workbook xmlns="%s" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>%s</sheets></workbook>' % (ns, ''.join(workbook)))
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(rels) + '</Relationships>')
    return output.getvalue()
