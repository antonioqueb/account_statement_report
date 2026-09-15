# -*- coding: utf-8 -*-
"""Carga ADD: tope de memoria del formulario multipart.

Odoo 19 fija `httprequest.max_form_memory_size = 10 MB` y werkzeug 3.0.1
aplica ese tope al cuerpo multipart completo (archivos incluidos), con el
parseo en modo silencioso: un ZIP mayor a 10 MB deja el formulario VACÍO,
Odoo no encuentra csrf_token y responde 400 "Session expired (invalid CSRF
token)" aunque la sesión esté viva. Aquí, antes de que el dispatcher lea
los parámetros, el tope sube al máximo real de carga de ADD (50 MiB, el
mismo client_max_body_size de nginx)."""
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
