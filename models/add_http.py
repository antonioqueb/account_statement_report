# -*- coding: utf-8 -*-
"""Carga ADD: tope de memoria del formulario multipart.

Odoo 19 fija `httprequest.max_form_memory_size = 10 MB`; werkzeug 3.0.1 no
lo aplica a los archivos (verificado el 15 sep 2026 con un cuerpo de 12 MB),
pero parsea en modo silencioso: cualquier cuerpo multipart INCOMPLETO
(archivo que cambió o seguía escribiéndose durante el envío) deja el
formulario vacío, Odoo no encuentra csrf_token y responde 400 "Session
expired (invalid CSRF token)" con la sesión viva. Ese fue el caso de los
lotes de XML recibidos del 15 sep. Subir el tope aquí es defensivo: deja
holgura real hasta el máximo de carga de ADD (50 MiB, el
client_max_body_size de nginx) sin depender del default de Odoo."""
from odoo import models
from odoo.http import request

ADD_UPLOAD_PATHS = ('/som/add/upload',)
ADD_UPLOAD_FORM_MEMORY = 50 * 1024 * 1024


class AddIrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _pre_dispatch(cls, rule, args):
        super()._pre_dispatch(rule, args)
        httprequest = getattr(request, 'httprequest', None)
        if httprequest is not None and httprequest.path in ADD_UPLOAD_PATHS:
            httprequest.max_form_memory_size = ADD_UPLOAD_FORM_MEMORY
            if (httprequest.max_content_length or 0) < ADD_UPLOAD_FORM_MEMORY:
                httprequest.max_content_length = ADD_UPLOAD_FORM_MEMORY
