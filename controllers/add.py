import json
from odoo import http
from odoo.http import request, content_disposition
from odoo.exceptions import AccessError, UserError
from werkzeug.exceptions import Forbidden, BadRequest


class AddController(http.Controller):
    def _context(self, companies):
        try:
            ids = json.loads(companies or '[]')
        except (ValueError, TypeError):
            raise BadRequest('Compañías inválidas.') from None
        if not isinstance(ids, list) or not ids or len(ids) > 100 or any(type(i) is not int for i in ids):
            raise BadRequest('Compañías inválidas.')
        # _guard intersects these with both Odoo and ADD grants.
        request.update_context(allowed_company_ids=ids)
        request.env['som.add.document']._guard()

    @http.route('/som/add/upload', type='http', auth='user', methods=['POST'], csrf=True, readonly=False)
    def upload(self, batch_id, companies=None, **kw):
        try:
            self._context(companies)
            batch = request.env['som.add.batch'].browse(int(batch_id)).exists()
            batch._owner_guard()
            limits = request.env['som.add.config']._for_company(batch.company_id)
            upload = request.httprequest.files.get('file')
            if not upload:
                raise BadRequest('Falta archivo.')
            raw = upload.stream.read(limits['upload_bytes'] + 1)
            result = batch._receive(upload.filename, raw)
            return request.make_json_response(result, headers=[('Cache-Control', 'no-store')])
        except AccessError:
            raise Forbidden('Sin autorización ADD.') from None
        except (UserError, ValueError) as error:
            return request.make_json_response({'error': str(error)}, status=400, headers=[('Cache-Control', 'no-store')])

    @http.route('/som/add/export', type='http', auth='user', methods=['POST'], csrf=True, readonly=False)
    def export(self, filters='{}', ids='[]', format='csv', columns='[]', companies=None, **kw):
        try:
            self._context(companies)
            body, mime, filename = request.env['som.add.document']._export_file(
                json.loads(filters), json.loads(ids), format, json.loads(columns))
            return request.make_response(body, headers=[('Content-Type', mime), ('Content-Disposition', content_disposition(filename)),
                         ('Cache-Control', 'no-store, private'), ('X-Content-Type-Options', 'nosniff')])
        except AccessError:
            raise Forbidden('Sin autorización ADD para exportar o descargar.') from None
        except (UserError, ValueError, TypeError) as error:
            raise BadRequest(str(error)) from None
