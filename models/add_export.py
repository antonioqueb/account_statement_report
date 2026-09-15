import json
from io import BytesIO
import zipfile
from odoo import api, models
from odoo.exceptions import AccessError, UserError
from ..add_services.export import csv_bytes, xlsx_bytes
from ..add_services.archive import safe_name

HEADERS = [('uuid', 'UUID'), ('company_id', 'Compañía'), ('perspective', 'Dirección'), ('fiscal_datetime', 'Fecha fiscal local'),
           ('emitter_rfc', 'RFC emisor'), ('emitter_name', 'Emisor'), ('receiver_rfc', 'RFC receptor'), ('receiver_name', 'Receptor'),
           ('series', 'Serie'), ('folio', 'Folio'), ('kind', 'Tipo'), ('currency', 'Moneda'), ('subtotal', 'Subtotal'),
           ('discount', 'Descuento'), ('vat', 'IVA global'), ('withheld', 'Retenciones globales'), ('total', 'Total'),
           ('method', 'Método'), ('payment_form', 'Forma'), ('complements', 'Complementos'), ('sat_state', 'Estado SAT'),
           ('classification', 'Categoría'), ('reference', 'Referencia'), ('sha256', 'SHA-256')]


class AddExport(models.Model):
    _inherit = 'som.add.document'

    @api.model
    def _export_file(self, filters, ids=None, format='csv', columns=None):
        self._guard(extraction=True)
        if format not in ('csv', 'xlsx', 'zip', 'xml'):
            raise UserError('Formato no permitido.')
        domain = self._filter_domain(filters)
        if ids:
            if not isinstance(ids, list) or len(ids) > 10000 or any(type(i) is not int for i in ids):
                raise UserError('Selección no válida.')
            domain += [('id', 'in', ids)]
        docs = self.with_context(active_test=False).search(domain, order='id', limit=10001)
        if len(docs) > 10000:
            raise UserError('Límite de exportación: 10,000 documentos. Reduzca el filtro.')
        if ids and set(ids) != set(docs.ids):
            raise AccessError('Algún documento seleccionado dejó de estar autorizado o dentro del filtro.')
        if not docs:
            raise UserError('No hay documentos en este alcance.')
        docs.check_access('read')
        if format in ('zip', 'xml'):
            if format == 'xml' and len(docs) != 1:
                raise UserError('Seleccione un documento para descargar XML.')
            size, output = 0, BytesIO()
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                for doc in docs:
                    raw = doc._raw()
                    size += len(raw)
                    if size > 100 * 1048576:
                        raise UserError('La descarga excede 100 MiB; reduzca la selección.')
                    archive.writestr('%s/%s.xml' % (doc.company_id.id, safe_name(doc.uuid)), raw)
            body = raw if format == 'xml' else output.getvalue()
            mime = 'application/xml' if format == 'xml' else 'application/zip'
            filename = safe_name(docs.uuid) + '.xml' if format == 'xml' else 'ADD-originales.zip'
        else:
            selected = [(k, label) for k, label in HEADERS if not columns or k in columns or k in ('uuid', 'company_id', 'currency', 'perspective')]
            headers = [label for _, label in selected] + ['Filtro temporal / alcance', 'Conversión']
            rows = []
            context_text = json.dumps(filters, ensure_ascii=False, sort_keys=True)
            sat_labels = dict(self._fields['sat_state'].selection)
            for doc in docs:
                row = []
                for key, _ in selected:
                    if key == 'company_id':
                        value = doc.company_id.name
                    elif key == 'perspective':
                        value = 'Emitido y recibido' if doc.issued and doc.received else ('Emitido' if doc.issued else 'Recibido')
                    elif key == 'sat_state':
                        value = sat_labels[doc.sat_state]
                    elif key in ('subtotal', 'discount', 'total'):
                        value = doc.parsed['header'].get({'subtotal': 'SubTotal', 'discount': 'Descuento', 'total': 'Total'}[key], '0')
                    else:
                        value = doc[key]
                    row.append(value)
                rows.append(row + [context_text, 'Sin conversión; moneda original del XML'])
            if format == 'csv':
                body, mime, filename = csv_bytes(headers, rows), 'text/csv; charset=utf-8', 'ADD-documentos.csv'
            else:
                sheets = [('Documentos', headers, rows)]
                specs = [
                    ('Conceptos', 'som.add.concept', ['sequence', 'sat_code', 'description', 'unit_code', 'quantity', 'unit_price', 'amount', 'discount', 'tax_object']),
                    ('Impuestos', 'som.add.tax', ['concept_id', 'payment_id', 'application_id', 'level', 'kind', 'tax', 'factor', 'rate', 'base', 'amount']),
                    ('Pagos', 'som.add.payment', ['sequence', 'payment_date', 'currency', 'amount', 'form', 'exchange_rate', 'operation']),
                    ('Aplicaciones', 'som.add.application', ['payment_id', 'target_uuid', 'target_id', 'currency', 'equivalence', 'partiality', 'previous', 'paid', 'balance']),
                    ('Relaciones', 'som.add.relation', ['kind', 'target_uuid', 'target_id']),
                ]
                total_children = 0
                for title, model, names in specs:
                    records = self.env[model].search([('document_id', 'in', docs.ids)], order='id', limit=100001)
                    total_children += len(records)
                    if total_children > 100000:
                        raise UserError('El detalle excede 100,000 filas; reduzca el filtro.')
                    detail = []
                    for record in records:
                        values = []
                        for key in names:
                            value = record[key]
                            values.append(value.id if isinstance(value, models.BaseModel) else value)
                        detail.append([record.id, record.document_id.uuid, record.company_id.name, record.currency] + values + [json.dumps(record.original or {}, ensure_ascii=False)])
                    sheets.append((title, ['ID estable', 'UUID documento', 'Compañía', 'Moneda'] + names + ['Valores originales del XML'], detail))
                body, mime, filename = xlsx_bytes(sheets), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'ADD-detallado.xlsx'
        # Recheck immediately before delivery; no generated files, token URLs,
        # public attachments or cached exports survive a permissions revocation.
        self._guard(extraction=True)
        docs.check_access('read')
        for company in docs.company_id:
            self.env['som.add.audit']._log(company, 'download' if format in ('xml', 'zip') else 'export', filename,
                                           len(docs.filtered(lambda d: d.company_id == company)))
        return body, mime, filename
