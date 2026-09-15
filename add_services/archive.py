"""Bounded in-memory upload expansion. Never extract a path to disk."""
from io import BytesIO
from pathlib import PurePosixPath
import re
import stat
import zipfile
from .cfdi import Rejected

DEFAULT_LIMITS = dict(xml_bytes=5 * 1024 * 1024, upload_bytes=25 * 1024 * 1024,
                      batch_bytes=100 * 1024 * 1024, entries=1000, ratio=100)


def safe_name(name):
    return re.sub(r'[^\w.() -]', '_', str(name).replace('\\', '/').split('/')[-1])[:150] or 'documento.xml'


def expand(name, raw, limits=None):
    """Yield (name, bytes, rejection). Container limit failures reject container."""
    lim = dict(DEFAULT_LIMITS, **(limits or {}))
    if not raw or len(raw) > lim['upload_bytes']:
        raise Rejected('size', 'Archivo vacío o mayor al límite de recepción.')
    if name.lower().endswith('.xml'):
        if len(raw) > lim['xml_bytes']:
            raise Rejected('size', 'XML mayor al límite.')
        return [(safe_name(name), raw, None)]
    if not name.lower().endswith('.zip'):
        raise Rejected('format', 'Solo se admiten XML y ZIP de XML.')
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > lim['entries'] or sum(i.file_size for i in entries) > lim['batch_bytes']:
                raise Rejected('limits', 'ZIP excede cantidad o tamaño descomprimido permitido.')
            result, total = [], 0
            for info in entries:
                if info.is_dir():
                    continue
                path = PurePosixPath(info.filename.replace('\\', '/'))
                invalid = (path.is_absolute() or '..' in path.parts or ':' in info.filename
                           or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1
                           or not info.filename.lower().endswith('.xml'))
                if invalid:
                    result.append((safe_name(info.filename), b'', 'Entrada ZIP no permitida (ruta, enlace, cifrado o extensión).'))
                    continue
                if info.file_size > lim['xml_bytes'] or info.file_size / max(1, info.compress_size) > lim['ratio']:
                    result.append((safe_name(info.filename), b'', 'Entrada ZIP excede tamaño o relación de compresión.'))
                    continue
                try:
                    with archive.open(info) as stream:
                        data = stream.read(lim['xml_bytes'] + 1)
                    total += len(data)
                    if len(data) > lim['xml_bytes'] or total > lim['batch_bytes']:
                        raise Rejected('limits', 'Límite real de descompresión excedido.')
                    result.append((safe_name(info.filename), data, None))
                except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
                    result.append((safe_name(info.filename), b'', 'Entrada ZIP dañada o compresión no admitida.'))
            return result
    except zipfile.BadZipFile:
        raise Rejected('structure', 'ZIP dañado.') from None
