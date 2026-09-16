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
        allowed = set(LIST_FIELDS)
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
            add('Impuestos globales documentados', '%s · %s · %s' % (doc_kind, 'Retención' if kind == 'withholding' else 'Traslado', tax), amount, cur,
                tax_domain + [('currency', '=', cur), ('document_kind', '=', doc_kind), ('kind', '=', kind), ('tax', '=', tax)], 'som.add.tax')
        line_domain = [('document_id', 'any', economic), ('level', '=', 'concept')]
        for cur, doc_kind, kind, tax, factor, rate, amount in self.env['som.add.tax']._read_group(line_domain, ['currency', 'document_kind', 'kind', 'tax', 'factor', 'rate'], ['amount:sum']):
            add('Impuestos por tasa · comprobación de conceptos', '%s · %s · %s · %s · %s' % (doc_kind, 'Retención' if kind == 'withholding' else 'Traslado', tax, factor or 'Sin factor', rate if rate is not False else 'Sin tasa'), amount, cur,
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

    # ══════════════════════════════════════════════════════════════════
    # Tablero v2 (15 sep 2026): series listas para graficar, al nivel de
    # SOM Analytics. Todo dominio de drill viaja con la serie para abrir
    # la lista nativa filtrada con un clic.
    # ══════════════════════════════════════════════════════════════════
    TAX_NAMES = {'001': 'ISR', '002': 'IVA', '003': 'IEPS'}

    @api.model
    def dashboard_v2(self, filters):
        filters = dict(filters or {})
        domain = self._filter_domain(filters)
        economic = self._filter_domain(filters, economic=True)
        docs = self.with_context(active_test=False)
        date_field = filters.get('date_basis', 'fiscal_date')
        measure = filters.get('measure', 'base')
        if measure not in ('base', 'total'):
            raise UserError('Medida no permitida.')
        direction = filters.get('direction')
        issued = direction == 'issued'
        rfc_field = 'receiver_rfc' if issued else 'emitter_rfc'
        name_field = 'receiver_name' if issued else 'emitter_name'

        def month_bounds(month):
            start = str(month)[:10]
            end = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
            return start, str(end)[:10]

        def key(month):
            return str(month)[:7] if month else 'Sin fecha'

        ie_domain = economic + [('kind', 'in', ['I', 'E'])]
        currencies = [c for c, _ in docs._read_group(ie_domain, ['currency'], ['__count'], order='__count DESC')]
        if not currencies:
            currencies = [c for c, _ in docs._read_group(domain, ['currency'], ['__count'], order='__count DESC')]
        out = dict(filters=filters, updated_at=str(fields.Datetime.now()), currencies=currencies,
                   primary=currencies[0] if currencies else 'MXN', monthly={}, counterparts={},
                   kpis=dict(invoices={}, adjustments={}, net={}, payments={}),
                   methods={}, taxes={}, concepts={}, payments={}, categories={})

        # ── Volumen y calidad (dominio completo, sin excluir cancelados) ──
        count = docs.search_count(domain)
        kinds = [dict(kind=k, count=n, domain=domain + [('kind', '=', k)]) for k, n in docs._read_group(domain, ['kind'], ['__count'])]
        sat = [dict(state=s, count=n, domain=domain + [('sat_state', '=', s)]) for s, n in docs._read_group(domain, ['sat_state'], ['__count'])]
        consistency = [dict(state=s, count=n, domain=domain + [('consistency', '=', s)]) for s, n in docs._read_group(domain, ['consistency'], ['__count'])]
        sat_map = {r['state']: r['count'] for r in sat}
        out['kinds'], out['sat'], out['consistency'] = kinds, sat, consistency
        out['kpis']['documents'] = dict(value=count, domain=domain)
        out['kpis']['sat_valid'] = dict(value=sat_map.get('valid', 0), total=count,
                                        pct=(100.0 * sat_map.get('valid', 0) / count) if count else 0.0,
                                        unknown=sat_map.get('unknown', 0), cancelled=sat_map.get('cancelled', 0),
                                        domain=domain + [('sat_state', '=', 'valid')])
        warn = sum(r['count'] for r in consistency if r['state'] in ('warning', 'partial'))
        out['kpis']['alerts'] = dict(value=warn, domain=domain + [('consistency', 'in', ['warning', 'partial'])])

        # ── Facturación por moneda ──
        for cur in currencies:
            scope = ie_domain + [('currency', '=', cur)]
            by_kind = {k: dict(amount=a, total=t, count=n) for k, a, t, n in docs._read_group(
                scope, ['kind'], [measure + ':sum', 'total:sum', '__count'])}
            inv, adj = by_kind.get('I', {}), by_kind.get('E', {})
            out['kpis'].setdefault('invoices', {})[cur] = dict(amount=inv.get('amount', 0.0), count=inv.get('count', 0), domain=scope + [('kind', '=', 'I')])
            out['kpis'].setdefault('adjustments', {})[cur] = dict(amount=adj.get('amount', 0.0), count=adj.get('count', 0), domain=scope + [('kind', '=', 'E')])
            out['kpis'].setdefault('net', {})[cur] = dict(amount=inv.get('amount', 0.0) - adj.get('amount', 0.0), domain=scope)

            # Evolución mensual I / E (+ conteo)
            months = {}
            for kind, month, amount, n in docs._read_group(scope, ['kind', date_field + ':month'], [measure + ':sum', '__count']):
                row = months.setdefault(key(month), dict(month=key(month), I=0.0, E=0.0, P=0.0, nI=0, nE=0, nP=0,
                                                          domain=scope + ([(date_field, '>=', month_bounds(month)[0]), (date_field, '<', month_bounds(month)[1])] if month else [(date_field, '=', False)])))
                row[kind] = amount
                row['n' + kind] = n
            out['monthly'][cur] = months

            # Contrapartes (pareto sobre facturas I)
            inv_scope = scope + [('kind', '=', 'I')]
            total = inv.get('amount', 0.0) or 0.0
            top = docs._read_group(inv_scope, [rfc_field, name_field], [measure + ':sum', '__count'], order=measure + ':sum DESC', limit=12)
            rows, cumulative = [], 0.0
            for rfc, name, amount, n in top:
                cumulative += amount or 0.0
                rows.append(dict(rfc=rfc or 'Sin RFC', name=name or rfc or 'Sin nombre', value=amount or 0.0, count=n,
                                 percent=(100.0 * (amount or 0.0) / total) if total else 0.0,
                                 cumulative=(100.0 * cumulative / total) if total else 0.0,
                                 domain=inv_scope + [(rfc_field, '=', rfc)]))
            others = total - sum(r['value'] for r in rows)
            out['counterparts'][cur] = dict(rows=rows, others=others, total=total,
                                            others_domain=inv_scope + [(rfc_field, 'not in', [r['rfc'] for r in rows])])

            # PUE / PPD
            out['methods'][cur] = [dict(method=m or 'Sin método', kind=k, amount=a, count=n, domain=scope + [('kind', '=', k), ('method', '=', m)])
                                   for k, m, a, n in docs._read_group(scope, ['kind', 'method'], [measure + ':sum', '__count'])]

            # Impuestos globales
            tax_domain = [('document_id', 'any', scope), ('level', 'in', ['global', 'local'])]
            taxes = []
            for doc_kind, kind, tax, amount in self.env['som.add.tax']._read_group(tax_domain, ['document_kind', 'kind', 'tax'], ['amount:sum']):
                label = '%s %s' % (self.TAX_NAMES.get(tax, tax or '?'), 'retenido' if kind == 'withholding' else 'trasladado')
                taxes.append(dict(label=label, kind=doc_kind, nature=kind, tax=tax, amount=amount,
                                  domain=tax_domain + [('document_kind', '=', doc_kind), ('kind', '=', kind), ('tax', '=', tax)]))
            out['taxes'][cur] = taxes

            # Conceptos (facturas I, comerciales) por clave SAT
            concept_domain = [('document_id', 'any', inv_scope), ('commercial', '=', True)]
            out['concepts'][cur] = [dict(code=code or 'Sin clave', unit=unit or '', amount=a, count=n, domain=concept_domain + [('sat_code', '=', code)])
                                    for code, unit, a, n in self.env['som.add.concept']._read_group(
                                        concept_domain, ['sat_code', 'unit_code'], ['amount:sum', '__count'], order='amount:sum DESC', limit=10)]
            if filters.get('direction') in ('issued', 'received'):
                out['categories'][cur] = [dict(category=c or 'Sin categoría', amount=a, count=n, domain=concept_domain + [('classification', '=', c)])
                                          for c, a, n in self.env['som.add.concept']._read_group(
                                              concept_domain, ['classification'], ['amount:sum', '__count'], order='amount:sum DESC', limit=8)]

        # ── Pagos documentados (FechaPago / MonedaP) ──
        payment_doc_domain = self._filter_domain(dict(filters, kind='P'), economic=True, dates=False, currency=False)
        payment_domain = [('document_id', 'any', payment_doc_domain)]
        for k, op in [('start', '>='), ('end', '<=')]:
            if filters.get(k):
                payment_domain.append(('payment_date', op, filters[k]))
        Payment = self.env['som.add.payment']
        for cur, month, amount, n in Payment._read_group(payment_domain, ['currency', 'payment_date:month'], ['amount:sum', '__count']):
            bounds = month_bounds(month) if month else None
            drill = payment_domain + [('currency', '=', cur)] + ([('payment_date', '>=', bounds[0]), ('payment_date', '<', bounds[1])] if bounds else [('payment_date', '=', False)])
            out['payments'].setdefault(cur, {})[key(month)] = dict(month=key(month), amount=amount, count=n, domain=drill)
            if cur in out['monthly']:
                row = out['monthly'][cur].setdefault(key(month), dict(month=key(month), I=0.0, E=0.0, P=0.0, nI=0, nE=0, nP=0, domain=drill))
                row['P'], row['nP'] = amount, n
            if cur not in currencies:
                currencies.append(cur)
        for cur in currencies:
            rows = out['payments'].get(cur, {})
            out['kpis'].setdefault('payments', {})[cur] = dict(amount=sum(r['amount'] for r in rows.values()), count=sum(r['count'] for r in rows.values()),
                                                               domain=payment_domain + [('currency', '=', cur)])
        apps = [('payment_id', 'any', payment_domain)]
        missing = apps + [('target_id', '=', False)]
        out['kpis']['applications'] = dict(value=self.env['som.add.application'].search_count(apps), domain=apps)
        out['kpis']['missing_uuid'] = dict(value=self.env['som.add.application'].search_count(missing), domain=missing)

        # ── PPD sin complemento de pago cargado ──
        ppd = economic + [('kind', '=', 'I'), ('method', '=', 'PPD')]
        covered = {t.id for (t,) in self.env['som.add.application']._read_group([('target_id', 'any', ppd)], ['target_id'], []) if t}
        open_domain = ppd + ([('id', 'not in', list(covered))] if covered else [])
        out['kpis']['ppd_open'] = dict(value=docs.search_count(open_domain), domain=open_domain,
                                       amounts={cur: a for cur, a in docs._read_group(open_domain, ['currency'], [measure + ':sum'])})

        # ── Relaciones faltantes ──
        unresolved = [('document_id', 'any', domain), ('target_id', '=', False)]
        out['kpis']['relations_missing'] = dict(value=self.env['som.add.relation'].search_count(unresolved), domain=unresolved)

        # ── Importaciones (fecha de carga) ──
        companies = filters.get('companies') or [self.env.company.id]
        item_domain = [('company_id', 'in', companies)]
        batch_domain = [('company_id', 'in', companies)]
        if direction in ('issued', 'received'):
            item_domain.append(('batch_id.direction', '=', direction))
            batch_domain.append(('direction', '=', direction))
        for k, op in [('start', '>='), ('end', '<=')]:
            if filters.get(k):
                item_domain.append(('create_date', op, filters[k] + (' 23:59:59' if k == 'end' else '')))
        Item = self.env['som.add.item']
        states = {s: n for s, n in Item._read_group(item_domain, ['state'], ['__count'])}
        out['kpis']['imports'] = dict(imported=states.get('imported', 0), duplicate=states.get('duplicate', 0),
                                     rejected=states.get('rejected', 0), failed=states.get('failed', 0), pending=states.get('pending', 0),
                                     domain=item_domain + [('state', 'in', ['rejected', 'failed'])])
        out['rejections'] = [dict(code=c or 'Sin código', count=n, domain=item_domain + [('code', '=', c), ('state', 'in', ['rejected', 'failed', 'duplicate'])])
                             for c, n in Item._read_group(item_domain + [('state', 'in', ['rejected', 'failed', 'duplicate'])], ['code'], ['__count'], order='__count DESC', limit=8)]
        batches = self.env['som.add.batch'].search(batch_domain, order='id desc', limit=8)
        out['batches'] = [dict(id=b.id, name=b.name, direction=b.direction, state=b.state, sealed=b.sealed, user=b.user_id.name,
                               created=str(b.create_date)[:16], total=b.total_count, imported=b.imported_count, duplicate=b.duplicate_count,
                               rejected=b.rejected_count, failed=b.failed_count, pending=b.pending_count) for b in batches]
        # ══════════════════════════════════════════════════════════════
        # ANÁLISIS (16 sep 2026): preguntas de negocio, no solo conteos.
        # ══════════════════════════════════════════════════════════════
        out['insights'] = self._dashboard_insights(filters, domain, economic, currencies, measure, date_field, rfc_field, name_field)
        out['notice'] = ('Perspectivas documentales; sin eliminaciones intragrupo. Monedas separadas. No equivale a contabilidad ni a '
                         'declaración fiscal. Los importes excluyen cancelados confirmados; pagos por FechaPago; incidencias por fecha de carga.')
        return out

    @api.model
    def _dashboard_insights(self, filters, domain, economic, currencies, measure, date_field, rfc_field, name_field):
        docs = self.with_context(active_test=False)
        today = fields.Date.context_today(self)
        direction = filters.get('direction')
        ins = dict(compare={}, aging={}, dso={}, movement={}, cohort={}, vat={}, forms={}, descriptions={}, adjustments_ratio={}, public={})
        ie_domain = economic + [('kind', 'in', ['I', 'E'])]

        def amount_by_cur(dom, kind='I'):
            return {c: dict(amount=a or 0.0, count=n) for c, a, n in docs._read_group(dom + [('kind', '=', kind)], ['currency'], [measure + ':sum', '__count'])}

        def rfcs_by_cur(dom):
            return {c: n for c, n in docs._read_group(dom + [('kind', '=', 'I')], ['currency'], [rfc_field + ':count_distinct'])}

        # ── Comparativo: periodo anterior de igual duración y mismo periodo del año anterior ──
        if filters.get('start') and filters.get('end'):
            start, end = date.fromisoformat(filters['start']), date.fromisoformat(filters['end'])
            days = (end - start).days + 1
            periods = {
                'current': (start, end),
                'previous': (start - timedelta(days=days), start - timedelta(days=1)),
                'year_ago': (date(start.year - 1, start.month, min(start.day, 28)), date(end.year - 1, end.month, min(end.day, 28))),
            }
            for key, (a, b) in periods.items():
                dom = self._filter_domain(dict(filters, start=str(a), end=str(b)), economic=True)
                inv = amount_by_cur(dom, 'I')
                adj = amount_by_cur(dom, 'E')
                rf = rfcs_by_cur(dom)
                for cur in currencies:
                    ins['compare'].setdefault(cur, {})[key] = dict(
                        start=str(a), end=str(b), invoices=inv.get(cur, {}).get('amount', 0.0), count=inv.get(cur, {}).get('count', 0),
                        adjustments=adj.get(cur, {}).get('amount', 0.0), counterparts=rf.get(cur, 0),
                        ticket=(inv.get(cur, {}).get('amount', 0.0) / inv.get(cur, {}).get('count', 1)) if inv.get(cur, {}).get('count') else 0.0,
                        domain=dom + [('kind', '=', 'I'), ('currency', '=', cur)])

        # ── Ajustes sobre facturación y Público en General ──
        for cur in currencies:
            scope = ie_domain + [('currency', '=', cur)]
            by_kind = {k: a or 0.0 for k, a in docs._read_group(scope, ['kind'], [measure + ':sum'])}
            ins['adjustments_ratio'][cur] = (100.0 * by_kind.get('E', 0.0) / by_kind['I']) if by_kind.get('I') else 0.0
            if direction == 'issued':
                pub = scope + [('kind', '=', 'I'), ('receiver_rfc', 'in', ['XAXX010101000', 'XEXX010101000'])]
                amount = sum(a or 0.0 for (a,) in docs._read_group(pub, [], [measure + ':sum']))
                ins['public'][cur] = dict(amount=amount, pct=(100.0 * amount / by_kind['I']) if by_kind.get('I') else 0.0, domain=pub)

        # ── Cartera documental PPD abierta AL DÍA DE HOY (sin filtro de fechas): saldo y antigüedad ──
        open_base = self._filter_domain(dict(filters, start='', end=''), economic=True) + [('kind', '=', 'I'), ('method', '=', 'PPD')]
        paid_by_doc = {}
        for target, paid in self.env['som.add.application']._read_group([('target_id', 'any', open_base)], ['target_id'], ['paid:sum']):
            if target:
                paid_by_doc[target.id] = paid or 0.0
        buckets = [('0-30', 0, 30), ('31-60', 31, 60), ('61-90', 61, 90), ('91-180', 91, 180), ('+180', 181, 10 ** 6)]
        rows = docs.search_read(open_base, ['id', 'total', 'fiscal_date', 'currency', rfc_field, name_field, 'uuid'], order='fiscal_date asc', limit=20000)
        aging = {}
        for r in rows:
            remaining = (r['total'] or 0.0) - paid_by_doc.get(r['id'], 0.0)
            if remaining <= 0.01:
                continue
            cur = r['currency'] or 'MXN'
            age = (today - r['fiscal_date']).days if r['fiscal_date'] else 0
            bucket = next(b for b, lo, hi in buckets if lo <= age <= hi)
            a = aging.setdefault(cur, dict(total=0.0, count=0, buckets={b: dict(amount=0.0, count=0) for b, _, _ in buckets}, counterparts={}, ids=[]))
            a['total'] += remaining; a['count'] += 1; a['ids'].append(r['id'])
            a['buckets'][bucket]['amount'] += remaining; a['buckets'][bucket]['count'] += 1
            cp = a['counterparts'].setdefault(r[rfc_field] or 'Sin RFC', dict(rfc=r[rfc_field] or 'Sin RFC', name=r[name_field] or '', amount=0.0, count=0, oldest=age, ids=[]))
            cp['amount'] += remaining; cp['count'] += 1; cp['oldest'] = max(cp['oldest'], age); cp['ids'].append(r['id'])
        for cur, a in aging.items():
            top = sorted(a['counterparts'].values(), key=lambda c: -c['amount'])[:10]
            for c in top:
                c['domain'] = [('id', 'in', c.pop('ids'))]
            a['counterparts'] = top
            a['domain'] = [('id', 'in', a.pop('ids'))]
            a['overdue_30'] = sum(v['amount'] for b, v in a['buckets'].items() if b != '0-30')
        ins['aging'] = aging

        # ── Días de cobro/pago documentales: FechaPago − fecha de la factura, ponderado por importe pagado ──
        pay_dom = [('target_id', 'any', economic + [('kind', '=', 'I')]), ('payment_id.payment_date', '!=', False)]
        for k, op in [('start', '>='), ('end', '<=')]:
            if filters.get(k):
                pay_dom.append(('payment_id.payment_date', op, filters[k]))
        apps = self.env['som.add.application'].search_read(pay_dom, ['paid', 'currency', 'payment_id', 'target_id'], limit=20000, load=None)
        if apps:
            pay_dates = {p.id: p.payment_date for p in self.env['som.add.payment'].browse(list({a['payment_id'] for a in apps}))}
            inv_dates = {d.id: (d.fiscal_date, d.currency) for d in docs.browse(list({a['target_id'] for a in apps}))}
            acc = {}
            for a in apps:
                pd_, (fd, cur) = pay_dates.get(a['payment_id']), inv_dates.get(a['target_id'], (None, None))
                if not pd_ or not fd or not a['paid']:
                    continue
                # La moneda que manda es la de la FACTURA (la de la aplicación
                # puede venir vacía o XXX en cargas anteriores a la corrección).
                cur = cur or a['currency'] or 'MXN'
                x = acc.setdefault(cur, dict(weighted=0.0, paid=0.0, n=0, over_30=0))
                days = (pd_ - fd).days
                x['weighted'] += days * a['paid']; x['paid'] += a['paid']; x['n'] += 1; x['over_30'] += 1 if days > 30 else 0
            for cur, x in acc.items():
                ins['dso'][cur] = dict(days=(x['weighted'] / x['paid']) if x['paid'] else 0.0, applications=x['n'], paid=x['paid'],
                                       over_30_pct=(100.0 * x['over_30'] / x['n']) if x['n'] else 0.0, domain=pay_dom + [('target_id.currency', '=', cur)])

        # ── Contrapartes que crecen / caen: últimos 90 días vs 90 anteriores (respecto al fin del periodo) ──
        end = date.fromisoformat(filters['end']) if filters.get('end') else today
        recent = (end - timedelta(days=89), end)
        prior = (end - timedelta(days=179), end - timedelta(days=90))
        base_nodates = self._filter_domain(dict(filters, start='', end=''), economic=True) + [('kind', '=', 'I')]
        for cur in currencies:
            def window(a, b):
                dom = base_nodates + [('currency', '=', cur), (date_field, '>=', str(a)), (date_field, '<=', str(b))]
                return {rfc: dict(name=name, amount=amt or 0.0) for rfc, name, amt in docs._read_group(dom, [rfc_field, name_field], [measure + ':sum'])}, dom
            now, now_dom = window(*recent)
            before, before_dom = window(*prior)
            moves = []
            for rfc in set(now) | set(before):
                cur_amt, prev_amt = now.get(rfc, {}).get('amount', 0.0), before.get(rfc, {}).get('amount', 0.0)
                if max(cur_amt, prev_amt) <= 0:
                    continue
                moves.append(dict(rfc=rfc or 'Sin RFC', name=(now.get(rfc) or before.get(rfc))['name'] or rfc or 'Sin nombre',
                                  recent=cur_amt, prior=prev_amt, delta=cur_amt - prev_amt,
                                  pct=((cur_amt - prev_amt) / prev_amt * 100.0) if prev_amt else None,
                                  domain=now_dom + [(rfc_field, '=', rfc)]))
            moves.sort(key=lambda m: m['delta'])
            ins['movement'][cur] = dict(up=[m for m in reversed(moves) if m['delta'] > 0][:6], down=[m for m in moves if m['delta'] < 0][:6],
                                        recent=[str(recent[0]), str(recent[1])], prior=[str(prior[0]), str(prior[1])],
                                        new=[m for m in reversed(moves) if m['prior'] == 0 and m['recent'] > 0][:6],
                                        lost=[m for m in moves if m['recent'] == 0 and m['prior'] > 0][:6])

        # ── Cohortes: contrapartes nuevas vs recurrentes por mes (primera factura en toda la historia) ──
        first_seen = {rfc: d for rfc, d in docs._read_group(base_nodates, [rfc_field], ['fiscal_date:min'])}
        for cur in currencies:
            months = {}
            for rfc, month, amt, n in docs._read_group(economic + [('kind', '=', 'I'), ('currency', '=', cur)], [rfc_field, date_field + ':month'], [measure + ':sum', '__count']):
                if not month:
                    continue
                key = str(month)[:7]
                row = months.setdefault(key, dict(month=key, new=0, recurring=0, new_amount=0.0, recurring_amount=0.0))
                fs = first_seen.get(rfc)
                is_new = bool(fs) and str(fs)[:7] == key
                row['new' if is_new else 'recurring'] += 1
                row['new_amount' if is_new else 'recurring_amount'] += amt or 0.0
            ins['cohort'][cur] = [months[k] for k in sorted(months)]

        # ── IVA trasladado (emitidos) vs IVA acreditable (recibidos) por mes, ambas direcciones, misma compañía ──
        both = self._filter_domain(dict(filters, direction='both'), economic=True)
        Tax = self.env['som.add.tax']
        for side, flag in (('issued', 'issued'), ('received', 'received')):
            tdom = [('document_id', 'any', both + [(flag, '=', True), ('kind', 'in', ['I', 'E'])]), ('level', 'in', ['global', 'local']), ('kind', '=', 'transfer'), ('tax', '=', '002')]
            for cur, kind, month, amt in Tax._read_group(tdom, ['currency', 'document_kind', 'fiscal_date:month'], ['amount:sum']):
                if not month:
                    continue
                key = str(month)[:7]
                row = ins['vat'].setdefault(cur, {}).setdefault(key, dict(month=key, issued=0.0, received=0.0))
                row[side] += (amt or 0.0) * (-1 if kind == 'E' else 1)
        for cur in list(ins['vat']):
            ins['vat'][cur] = [dict(r, net=r['issued'] - r['received']) for _, r in sorted(ins['vat'][cur].items())]

        # ── Formas de pago (facturas I) ──
        FORMS = {'01': 'Efectivo', '02': 'Cheque', '03': 'Transferencia', '04': 'Tarjeta de crédito', '28': 'Tarjeta de débito', '99': 'Por definir', '30': 'Aplicación de anticipos'}
        for cur in currencies:
            scope = economic + [('kind', '=', 'I'), ('currency', '=', cur)]
            ins['forms'][cur] = [dict(code=f or '—', label=FORMS.get(f, f or 'Sin forma'), amount=a or 0.0, count=n, domain=scope + [('payment_form', '=', f)])
                                 for f, a, n in docs._read_group(scope, ['payment_form'], [measure + ':sum', '__count'], order=measure + ':sum DESC')]

        # ── Descripciones de concepto que más pesan (facturas I) ──
        for cur in currencies:
            cdom = [('document_id', 'any', economic + [('kind', '=', 'I'), ('currency', '=', cur)]), ('commercial', '=', True)]
            ins['descriptions'][cur] = [dict(description=(d or 'Sin descripción')[:80], amount=a or 0.0, count=n, domain=cdom + [('description', '=', d)])
                                        for d, a, n in self.env['som.add.concept']._read_group(cdom, ['description'], ['amount:sum', '__count'], order='amount:sum DESC', limit=12)]
        return ins
