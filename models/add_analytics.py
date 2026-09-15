from datetime import date, timedelta
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain
from ..add_services.analytics import concentration

LIST_FIELDS = ['uuid', 'company_id', 'fiscal_date', 'emitter_name', 'receiver_name', 'emitter_rfc', 'receiver_rfc',
               'series', 'folio', 'kind', 'currency', 'subtotal', 'discount', 'vat', 'withheld', 'total', 'method',
               'payment_form', 'complements', 'sat_state', 'consistency', 'batch_id', 'classification', 'reference',
               'stamp_date', 'create_date', 'issued', 'received', 'labels']


class AddExplorer(models.Model):
    _inherit = 'som.add.document'

    @api.model
    def bootstrap(self):
        scope = self._guard(company=self.env.company)
        return dict(companies=[{'id': c.id, 'name': c.name} for c in scope],
                    company_id=self.env.company.id,
                    operator=self.env.user.has_group('account_statement_report.group_add_operator'),
                    admin=self.env.user.has_group('account_statement_report.group_add_admin'),
                    export=self.env.user.has_group('account_statement_report.group_add_export'), uid=self.env.uid,
                    today=str(fields.Date.context_today(self)))

    @api.model
    def _filter_domain(self, filters, economic=False, dates=True, currency=True):
        scope = self._guard()
        filters = filters or {}
        if filters.get('start') and filters.get('end') and filters['start'] > filters['end']:
            raise UserError('La fecha inicial no puede ser posterior a la final.')
        companies = filters.get('companies') or [self.env.company.id]
        if not isinstance(companies, list) or any(type(c) is not int or c not in scope.ids for c in companies):
            raise UserError('Seleccione únicamente compañías activas y autorizadas ADD.')
        domain = Domain([('company_id', 'in', companies), ('active', '=', bool(filters.get('archived') is not True))])
        direction = filters.get('direction')
        if direction in ('issued', 'received'):
            domain &= Domain([(direction, '=', True)])
        elif direction not in (False, None, '', 'both'):
            raise UserError('Dirección inválida.')
        base_date = filters.get('date_basis', 'fiscal_date')
        if base_date not in ('fiscal_date', 'stamp_date', 'create_date'):
            raise UserError('Base temporal inválida.')
        if dates:
            for key, operator in [('start', '>='), ('end', '<=')]:
                value = filters.get(key)
                if value:
                    date.fromisoformat(value)
                    domain &= Domain([(base_date, operator, value + (' 23:59:59' if key == 'end' and base_date == 'create_date' else ''))])
        mapping = dict(kind='kind', method='method', form='payment_form', use='cfdi_use', sat='sat_state',
                       consistency='consistency', batch='batch_id', classification='classification')
        if currency:
            mapping['currency'] = 'currency'
        for key, field in mapping.items():
            value = filters.get(key)
            if value:
                domain &= Domain([(field, '=', int(value) if key == 'batch' else str(value)[:150])])
        if filters.get('complement'):
            domain &= Domain([('complements', 'ilike', str(filters['complement'])[:100])])
        if filters.get('label'):
            domain &= Domain([('labels', 'ilike', str(filters['label'])[:100])])
        if filters.get('tax'):
            domain &= Domain([('tax_ids.tax', '=', str(filters['tax'])[:30])])
        if filters.get('q'):
            query = str(filters['q'])[:200]
            domain &= Domain.OR([Domain([(field, 'ilike', query)]) for field in
                                 ('uuid', 'emitter_rfc', 'receiver_rfc', 'emitter_name', 'receiver_name', 'series', 'folio', 'concept_ids.description', 'reference')])
        if filters.get('valid_only'):
            domain &= Domain([('sat_state', '=', 'valid')])
        if economic:
            domain &= Domain([('sat_state', '!=', 'cancelled')])
        return list(domain)

    @api.model
    def explore(self, filters, offset=0, limit=50, order='fiscal_date desc, id desc'):
        domain = self._filter_domain(filters)
        allowed = set(LIST_FIELDS) - {'company_id', 'batch_id'}
        for clause in order.split(','):
            parts = clause.strip().split()
            if len(parts) != 2 or parts[0] not in allowed | {'id'} or parts[1].lower() not in ('asc', 'desc'):
                raise UserError('Orden no permitido.')
        records = self.with_context(active_test=False).search(domain, offset=max(0, int(offset)), limit=min(100, max(1, int(limit))), order=order)
        totals = self.with_context(active_test=False)._read_group(domain + [('kind', 'in', ['I', 'E'])], ['currency', 'kind'], ['total:sum', 'base:sum', '__count'])
        return dict(rows=records.read(LIST_FIELDS), count=self.with_context(active_test=False).search_count(domain),
                    totals=[dict(currency=c, kind=k, total=t, base=b, count=n) for c, k, t, b, n in totals], domain=domain)

    @api.model
    def dashboard(self, filters):
        domain = self._filter_domain(filters)
        economic = self._filter_domain(filters, economic=True)
        docs = self.with_context(active_test=False)
        rows = []
        def add(section, label, value, unit, drill, model='som.add.document', **extra):
            rows.append(dict(section=section, label=str(label or 'Sin dato'), value=value, unit=unit or '', domain=drill, model=model, **extra))
        count = docs.search_count(domain)
        date_field = filters.get('date_basis', 'fiscal_date')
        date_label = {'fiscal_date': 'fecha fiscal', 'stamp_date': 'timbrado', 'create_date': 'carga'}[date_field]
        add('Volumen documental', 'CFDI únicos', count, 'documentos', domain)
        for kind, month, n in docs._read_group(domain, ['kind', date_field + ':month'], ['__count']):
            start = str(month)[:10] if month else False
            end = (month.replace(day=28) + timedelta(days=4)).replace(day=1) if month else False
            drill = domain + [('kind', '=', kind)] + ([(date_field, '>=', start), (date_field, '<', str(end)[:10])] if month else [(date_field, '=', False)])
            add('Volumen por mes y tipo · ' + date_label, '%s · %s' % (str(month)[:7], kind), n, 'documentos', drill)
        measure = filters.get('measure', 'base')
        if measure not in ('base', 'total'):
            raise UserError('Medida no permitida.')
        for cur, kind, total, base, n in docs._read_group(economic + [('kind', 'in', ['I', 'E'])], ['currency', 'kind'], ['total:sum', 'base:sum', '__count']):
            add('Facturación y ajustes', '%s · %s' % (kind, cur), total if measure == 'total' else base, cur,
                economic + [('kind', '=', kind), ('currency', '=', cur)], count=n)
        if filters.get('net'):
            for cur, amount in docs._read_group(economic + [('kind', 'in', ['I', 'E'])], ['currency'], ['net_base:sum']):
                add('Base neta documental I − E · no es utilidad', cur, amount, cur,
                    economic + [('kind', 'in', ['I', 'E']), ('currency', '=', cur)])
        if filters.get('mxn'):
            converted = economic + [('kind', 'in', ['I', 'E']), ('mxn_available', '=', True)]
            for kind, source, amount, n in docs._read_group(converted, ['kind', 'conversion_source'], [measure + '_mxn:sum', '__count']):
                add('Comparación analítica MXN · conversión histórica', '%s · %s' % (kind, source), amount, 'MXN',
                    converted + [('kind', '=', kind), ('conversion_source', '=', source)], count=n)
            excluded = economic + [('kind', 'in', ['I', 'E']), ('mxn_available', '=', False)]
            add('Comparación analítica MXN · conversión histórica', 'Excluidos por falta de tipo de cambio', docs.search_count(excluded), 'documentos', excluded)
        if filters.get('compare') and filters.get('start') and filters.get('end'):
            start, end = date.fromisoformat(filters['start']), date.fromisoformat(filters['end'])
            days = (end - start).days + 1
            previous_filters = dict(filters, start=str(start - timedelta(days=days)), end=str(start - timedelta(days=1)))
            previous = self._filter_domain(previous_filters, economic=True) + [('kind', 'in', ['I', 'E'])]
            for cur, kind, amount in docs._read_group(previous, ['currency', 'kind'], [measure + ':sum']):
                add('Periodo anterior de igual duración · %s a %s' % (previous_filters['start'], previous_filters['end']),
                    kind, amount, cur, previous + [('kind', '=', kind), ('currency', '=', cur)])
        for cur, kind, month, amount in docs._read_group(economic + [('kind', 'in', ['I', 'E'])], ['currency', 'kind', date_field + ':month'], [measure + ':sum']):
            if not month:
                add('Evolución mensual · ' + date_label, 'Sin fecha · ' + kind, amount, cur,
                    economic + [('currency', '=', cur), ('kind', '=', kind), (date_field, '=', False)])
                continue
            end = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
            add('Evolución mensual · ' + date_label, '%s · %s' % (str(month)[:7], kind), amount, cur,
                economic + [('currency', '=', cur), ('kind', '=', kind), (date_field, '>=', str(month)[:10]), (date_field, '<', str(end)[:10])])
        # I and E stay separate; no currency or sign ambiguity in concentration.
        counterpart = 'receiver_rfc' if filters.get('direction') == 'issued' else 'emitter_rfc'
        if filters.get('direction') in ('issued', 'received'):
            universes = docs._read_group(economic + [('kind', '=', 'I')], ['currency'], [measure + ':sum'])
            for cur, total in universes:
                scope = economic + [('currency', '=', cur), ('kind', '=', 'I')]
                top = docs._read_group(scope, [counterpart], [measure + ':sum'], order=measure + ':sum DESC', limit=10)
                top_rows = concentration([dict(label=rfc, value=value) for rfc, value in top], str(total))
                for row in top_rows:
                    add('Concentración · facturas I', row['label'], row['value'], cur, scope + [(counterpart, '=', row['label'])], percent=row['percent'], cumulative=row['cumulative'])
                others = total - sum(v for _, v in top)
                if others:
                    add('Concentración · facturas I', 'Otros', others, cur, scope + [(counterpart, 'not in', [r for r, _ in top])])
        for cur, kind, method, total, n in docs._read_group(economic + [('kind', 'in', ['I', 'E'])], ['currency', 'kind', 'method'], [measure + ':sum', '__count']):
            add('PUE / PPD · clasificación documental', '%s · %s' % (method or 'Sin método', kind), total, cur,
                economic + [('currency', '=', cur), ('kind', '=', kind), ('method', '=', method)], count=n)
        # Domain('document_id', 'any', ...) compiles a subquery with ORM security,
        # avoiding a 100k-ID list or repeated invoice totals through joins.
        tax_domain = [('document_id', 'any', economic), ('level', 'in', ['global', 'local'])]
        for cur, doc_kind, kind, tax, amount in self.env['som.add.tax']._read_group(tax_domain, ['currency', 'document_kind', 'kind', 'tax'], ['amount:sum']):
            add('Impuestos globales documentados', '%s · %s · %s' % (doc_kind, kind, tax), amount, cur,
                tax_domain + [('currency', '=', cur), ('document_kind', '=', doc_kind), ('kind', '=', kind), ('tax', '=', tax)], 'som.add.tax')
        line_domain = [('document_id', 'any', economic), ('level', '=', 'concept')]
        for cur, doc_kind, kind, tax, factor, rate, amount in self.env['som.add.tax']._read_group(line_domain, ['currency', 'document_kind', 'kind', 'tax', 'factor', 'rate'], ['amount:sum']):
            add('Impuestos por tasa · comprobación de conceptos', '%s · %s · %s · %s · %s' % (doc_kind, kind, tax, factor or 'Sin factor', rate if rate is not False else 'Sin tasa'), amount, cur,
                line_domain + [('currency', '=', cur), ('document_kind', '=', doc_kind), ('kind', '=', kind), ('tax', '=', tax), ('factor', '=', factor), ('rate', '=', rate)], 'som.add.tax')
        payment_doc_domain = self._filter_domain(dict(filters, kind='P'), economic=True, dates=False, currency=False)
        payment_domain = [('document_id', 'any', payment_doc_domain)]
        for key, op in [('start', '>='), ('end', '<=')]:
            if filters.get(key):
                payment_domain.append(('payment_date', op, filters[key]))
        if filters.get('currency'):
            payment_domain.append(('currency', '=', filters['currency']))
        for cur, month, amount, n in self.env['som.add.payment']._read_group(payment_domain, ['currency', 'payment_date:month'], ['amount:sum', '__count']):
            end = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
            drill = payment_domain + [('currency', '=', cur), ('payment_date', '>=', str(month)[:10]), ('payment_date', '<', str(end)[:10])]
            add('Pagos documentados · FechaPago / MonedaP', str(month)[:7], amount, cur, drill, 'som.add.payment', count=n)
        apps = [('payment_id', 'any', payment_domain)]
        missing = apps + [('target_id', '=', False)]
        add('Calidad documental', 'Aplicaciones con UUID no cargado · FechaPago', self.env['som.add.application'].search_count(missing), 'aplicaciones', missing, 'som.add.application')
        add('Pagos documentados · FechaPago / MonedaP', 'Aplicaciones', self.env['som.add.application'].search_count(apps), 'aplicaciones', apps, 'som.add.application')
        concept_domain = [('document_id', 'any', economic), ('commercial', '=', True), ('document_kind', '=', 'I')]
        for cur, code, unit, amount in self.env['som.add.concept']._read_group(concept_domain, ['currency', 'sat_code', 'unit_code'], ['amount:sum'], order='amount:sum DESC', limit=20):
            add('Conceptos · clave SAT y unidad (sin homologación de productos)', '%s · %s' % (code, unit), amount, cur,
                concept_domain + [('currency', '=', cur), ('sat_code', '=', code), ('unit_code', '=', unit)], 'som.add.concept')
        if filters.get('direction') in ('issued', 'received'):
            for cur, category, rfc, amount in self.env['som.add.concept']._read_group(concept_domain, ['currency', 'classification', counterpart], ['amount:sum'], order='amount:sum DESC', limit=20):
                add('Conceptos · categoría y contraparte (facturas I)', '%s · %s' % (category or 'Sin categoría', rfc), amount, cur,
                    concept_domain + [('currency', '=', cur), ('classification', '=', category), (counterpart, '=', rfc)], 'som.add.concept')
        for field, value, label in [('sat_state', 'unknown', 'No consultados ante el SAT'), ('consistency', 'warning', 'Con alertas'), ('consistency', 'partial', 'Validación parcial')]:
            drill = domain + [(field, '=', value)]
            add('Calidad documental', label, docs.search_count(drill), 'documentos', drill)
        unresolved = [('document_id', 'any', domain), ('target_id', '=', False)]
        add('Calidad documental', 'Relaciones fiscales faltantes', self.env['som.add.relation'].search_count(unresolved), 'relaciones', unresolved, 'som.add.relation')
        # Import quality uses load date, explicitly independent of fiscal dates.
        item_domain = [('company_id', 'in', (filters.get('companies') or [self.env.company.id]))]
        if filters.get('direction') in ('issued', 'received'):
            item_domain.append(('batch_id.direction', '=', filters['direction']))
        for key, op in [('start', '>='), ('end', '<=')]:
            if filters.get(key):
                item_domain.append(('create_date', op, filters[key] + (' 23:59:59' if key == 'end' else '')))
        for code, n in self.env['som.add.item']._read_group(item_domain + [('state', 'in', ['duplicate', 'rejected', 'failed'])], ['code'], ['__count']):
            add('Incidencias de importación · fecha de carga', code, n, 'archivos', item_domain + [('code', '=', code)], 'som.add.item')
        return dict(rows=rows, count=count, updated_at=str(fields.Datetime.now()), filters=filters,
                    notice='Suma de perspectivas documentales; sin eliminaciones intragrupo. Monedas separadas. No equivale a contabilidad, flujo bancario ni declaración fiscal. Los importes económicos excluyen cancelados confirmados; los demás no implican vigencia SAT.')
