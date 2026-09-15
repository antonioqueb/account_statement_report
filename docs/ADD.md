# Visor ADD privado — Odoo 19

## Alcance y estado de verificación

Implementado dentro de **`Ventas/account_statement_report`**, versión **19.0.4.0.1**.
El addon conserva sus dependencias y reportes anteriores, y continúa con
`application=False`. ADD es un menú raíz independiente con icono propio.
No se creó otro addon ni otro manifiesto.

La versión/edición detectada es **Odoo 19 Enterprise según el README del repositorio**,
confirmada por los manifiestos y los patrones de código locales. No se inspeccionó
una base ejecutándose: el inventario de módulos instalados, la compilación de OWL,
la actualización y las rutas HTTP reales deben verificarse en desarrollo.
Por instrucción del usuario no se descargó Odoo ni se instaló o desplegó nada.

**El ADD es un visor documental autónomo.** No crea ni vincula facturas, asientos,
pagos contables, pedidos, contactos, productos o movimientos de inventario. Las
relaciones son exclusivamente entre XML de la misma compañía. No incluye
conectores SAT, e.firma, notificaciones, correo, portal ni servicios externos.

Se ejecutaron **26 pruebas del parser/archivos/exportadores y 7 comprobaciones
estáticas**, con resultado satisfactorio. Se incluye una suite de **16 pruebas
Odoo** pendiente de ejecución en la instalación del usuario. No se presenta la
seguridad de las rutas HTTP, la concurrencia PostgreSQL ni la interfaz como
verificadas en ejecución. Los ocho XML originales no estaban disponibles; todos
los ejemplos de pruebas son sintéticos, sin firmas auténticas.

## Archivos y arquitectura

| Archivos | Responsabilidad |
|---|---|
| `__manifest__.py`, `__init__.py`, `models/__init__.py` | Integración en el addon existente y carga de recursos |
| `add_services/cfdi.py` | Parser offline por URI, Decimal, validaciones, Carta Porte, árbol seguro y referencias extraídas |
| `add_services/archive.py` | Recepción limitada, ZIP en memoria, nombres seguros, rechazo de entradas peligrosas |
| `add_services/analytics.py` | Funciones numéricas puras y comprobación de referencias sintéticas |
| `add_services/export.py` | CSV protegido y XLSX nativo, sin dependencias nuevas |
| `models/add_security.py` | Roles, concesiones por usuario, almacenamiento privado, auditoría, límites y bloqueo de adjuntos genéricos |
| `models/add_document.py` | Documento, conceptos, impuestos, pagos, aplicaciones, relaciones y reprocesamiento |
| `models/add_import.py` | Lotes, recepción, persistencia por bloques, cancelación, reintentos y limpieza |
| `models/add_analytics.py` | Filtros, paginación, agregados ORM y dominios de desglose |
| `models/add_export.py` | Exportación autorizada de selección/filtro, XML original y ZIP |
| `controllers/add.py` | Dos rutas POST con sesión, CSRF y autorización por solicitud |
| `security/add_groups.xml`, `security/add/ir.model.access.csv`, `security/add_rules.xml` | Grupos explícitos, ACL específicas y reglas globales |
| `views/add_views.xml`, `data/add_cron.xml` | Menú ADD, vistas nativas, concesiones y tareas periódicas |
| `static/src/add/explorer.{js,xml,scss}`, `static/description/add_icon.svg` | Visor OWL, panel lateral, tablero, carga y estilos |
| `tests/fixtures_add.py`, `test_add_offline.py`, `test_add_structure.py` | Casos sintéticos y verificaciones locales |
| `tests/test_add_backend.py`, `tests/__init__.py` | Suite de permisos, alcance, persistencia e integridad para Odoo 19 |
| `tests/benchmark_add.py` | Medición aislada del parser, sin datos privados |

Se reutilizan ORM, `ir.cron`, acciones/vistas, servicio `orm`, servicio `action`,
notificaciones y OWL de Odoo. No se reutilizan los flujos contables ni los permisos
amplios de otros módulos. Las ACL ADD no conceden acceso a `base.group_user`.

## Actualización en desarrollo (a cargo del usuario)

La versión 19.0.4.0.1 corrige el error de actualización `KeyError: 'add_access'`:
Odoo obtiene el modelo destino del nombre del CSV. Las ACL ADD se cargan desde
`security/add/ir.model.access.csv`, después de sus grupos, conservando los mismos
identificadores y permisos. El CSV de permisos anterior del asistente permanece
independiente. También se distinguen las etiquetas de tipo CFDI, impuesto y relación.

Usar una copia de pruebas con las dependencias existentes ya instaladas. Ejemplo
con el ejecutable y configuración **de su instalación**, ajustando las rutas y
el nombre de la base:

```sh
/ruta/odoo-bin -c /ruta/odoo.conf -d ADD_DESARROLLO -u account_statement_report --stop-after-init --no-http
```

El `addons_path` debe incluir el directorio que contiene `account_statement_report`
y las rutas existentes de sus dependencias. Reiniciar los procesos de esa
instalación de desarrollo después de actualizar. No instalar un addon llamado
ADD: la actualización es de `account_statement_report`.

Pruebas del servidor en una base desechable:

```sh
/ruta/odoo-bin -c /ruta/odoo.conf -d ADD_PRUEBAS -u account_statement_report --test-enable --test-tags /account_statement_report --stop-after-init --no-http
```

El ejemplo no ejecuta cambios por sí mismo; estos comandos no se corrieron
durante esta entrega. No usar una base de producción para las pruebas.

## Asignación inicial de acceso

1. Un usuario que ya pueda administrar usuarios/permisos asigna explícitamente
   el permiso **ADD: gestión de accesos (requiere administrar usuarios)** a la
   persona que gestionará las concesiones. Esta acción se hace en la interfaz
   habitual de administración de Odoo; no hay un mecanismo de autoasignación ADD.
2. Asignar a cada usuario interno el rol **Consulta ADD**, **Operador ADD** o
   **Administrador ADD**. El rol documental no implica permiso de administrar usuarios.
3. En el formulario del usuario, en **Autorización explícita ADD**, seleccionar
   sus **Compañías autorizadas ADD**. El servidor exige simultáneamente
   `base.group_erp_manager` y el permiso independiente ADD para cambiar esa lista.
4. Cuando proceda, asignar aparte **ADD: exportar y descargar**.
5. Seleccionar en Odoo una compañía activa que esté autorizada también para ADD.
   Abrir el menú raíz **ADD**. El tablero inicia en esa compañía actual.

No se asignan grupos o compañías ADD automáticamente al instalar o actualizar.
Un rol sin concesiones de compañía no permite consultar documentos.

### Matriz de permisos

| Operación | Consulta | Operador | Administrador documental |
|---|---|---|---|
| Ver documentos, desglose, XML como texto y gráficos del alcance | Sí | Sí | Sí |
| Crear y cargar lotes propios | No | Sí | Sí |
| Modificar categoría, etiquetas, referencia y notas (con auditoría) | No | Sí | Sí |
| Reprocesar originales autorizados | No | Sí | Sí |
| Continuar/reintentar/cancelar lotes | No | Propios | Autorizados; el proceso sigue ejecutándose como propietario original |
| Archivar con motivo | No | No | Sí |
| Modificar límites por compañía | No | No | Sí |
| Editar datos fiscales/XML mediante RPC | No | No | No |
| Borrar documentos/evidencias | No | No | No |
| Exportar CSV/XLSX o descargar XML/ZIP | Solo con permiso adicional | Solo con permiso adicional | Solo con permiso adicional |
| Asignar roles/concesiones | Requiere administración efectiva de usuarios; concesiones requieren además gestión de accesos ADD | Igual | Igual |

### Controles de seguridad

- Alcance efectivo: compañías del usuario en Odoo ∩ compañías activas solicitadas
  ∩ concesiones explícitas ADD. Las reglas **globales**, sin grupos, se aplican a
  documentos, hijos, lotes, resultados, auditoría y configuración. No pueden
  ampliarse mediante la unión con otra regla de grupo. Portal/público quedan excluidos.
- El cambio de concesiones invalida la caché de reglas de Odoo. No hay caché de
  documentos, XML ni agregados en el navegador o en una tabla analítica.
- Los filtros, sugerencias y desgloses consultan modelos con las mismas reglas.
  No hay SQL de análisis que omita la seguridad. El único SQL explícito bloquea
  una fila de lote ya autorizada, con parámetros.
- Los métodos públicos verifican rol/alcance. Las mutaciones internas usan una
  capacidad Python por identidad, imposible de fabricar con un contexto JSON.
  Los `default_*` enviados por RPC se eliminan de las creaciones internas para
  impedir que inventen estado SAT, propietario de lote o banderas de archivo.
  El operador no puede usar `create/write/unlink` para editar datos fiscales.
- Los originales están en **`som.add.vault`**, campo binario en base de datos
  (`attachment=False`), sin ACL de usuario. No se generan `ir.attachment`, tokens
  públicos, seguidores ni copias en chatter. Se bloquea crear/reasignar adjuntos
  genéricos hacia modelos ADD.
- La lectura técnica de un original eleva únicamente el acceso a su almacén
  privado después de comprobar el documento y compañía. La auditoría técnica
  fija actor y compañía; no acepta valores de actor por RPC.
- Las tareas técnicas enumeran metadatos de lotes y ejecutan el trabajo como su
  propietario original, en una compañía fija, comprobando revocaciones. La
  limpieza técnica solo elimina archivos no incorporados vencidos.
- Exportación genérica de Odoo: `export_data` también exige el permiso de
  extracción, comprueba registros, neutraliza fórmulas y registra auditoría.
- Las rutas `/som/add/upload` y `/som/add/export` requieren sesión y POST con
  CSRF. Cada exportación se genera y entrega en la misma solicitud, sin archivos
  persistidos ni URL reutilizable. Se comprueba permiso antes de generar y antes
  de entregar. No hay exportaciones asíncronas en esta versión.
- Ver XML permite copiarlo manualmente; el permiso adicional controla las
  funciones de extracción, no la información ya mostrada ni un archivo ya descargado.
- El superusuario técnico y quienes administran servidor/BD conservan los
  privilegios propios de la plataforma. Este diseño no promete aislamiento del
  dueño de la base de datos.

El diseño toma como referencia la composición aditiva de ACL y el comportamiento
permisivo por defecto de reglas descritos en la [seguridad oficial de Odoo 19](https://www.odoo.com/documentation/19.0/developer/reference/backend/security.html).
Los bloques y agregaciones siguen las pautas de [rendimiento de Odoo 19](https://www.odoo.com/documentation/19.0/developer/reference/backend/performance.html).

## Uso del visor

### Recibidos y emitidos

1. Abrir **Recibidos → Cargar XML recibidos**, o **Emitidos → Cargar XML emitidos**.
2. Elegir compañía y seleccionar varios XML/ZIP, o arrastrarlos al área de carga.
   La dirección permanece visible y queda fijada con la compañía al crear el lote.
3. Pulsar **Crear lote e importar**. Se muestra progreso real de recepción de cada
   archivo y progreso de resultados persistidos. El parser actúa al recibir;
   la persistencia documental continúa por bloques de 20 y por `ir.cron` si el
   navegador deja un lote sellado pendiente de terminar.
4. Consultar **Ver resultados por archivo** o **Importaciones**. Un inválido no
   revierte los archivos correctos. Un ZIP mal formado o que supera el límite del
   contenedor cuenta como un rechazo del archivo contenedor.
5. Si la navegación se interrumpe antes de cerrar recepción, abrir el lote y usar
   **Cerrar recepción e iniciar**. Reintentar procesa pendientes/fallidos con
   contenido conservado. No reinterpreta rechazos de RFC/dirección como válidos.
6. Cancelar conserva lo ya incorporado; lo pendiente sigue identificado como tal.

La igualdad de RFC usa trim/mayúsculas y conserva el original. No se asigna otra
compañía automáticamente. El mismo emisor/receptor propio genera un documento
con ambas condiciones. La clave SQL única es **compañía + UUID normalizado**,
nunca compañía + dirección + UUID. Se conserva SHA-256 de los bytes recibidos.

Mismo UUID/hash: duplicado omitido y enlace al original. Mismo UUID/hash distinto:
rechazo por conflicto con hash de entrada para revisión; no se reemplaza el
original ni se infiere fraude. La copia de entrada duplicada/conflictiva se
descarta; la evidencia de resultado y hash permanece. En conflictos concurrentes
sin visibilidad del ganador, el ítem queda pendiente para la siguiente transacción.

### Navegación y detalle

- Búsqueda en servidor por UUID, RFC, nombres, serie, folio, concepto o referencia.
- Filtros combinables; paginación de 50 filas (servidor limita a 100), encabezado
  y primera columna fijos, scroll horizontal, orden y anchos de columna ajustables.
- Selección por página y totales de esa selección separados del universo filtrado.
  CSV/ZIP/XLSX usan la selección; sin selección usan todo el filtro dentro del límite.
- Preferencias y vistas privadas guardadas por usuario **en este navegador**.
  No se almacenan XML ni resultados en `localStorage`. No hay intercambio de vistas.
- Agrupar abre las vistas nativas de Odoo con el mismo dominio. Las listas no
  suman automáticamente monedas incompatibles.
- Panel lateral con pestañas, anterior/siguiente entre páginas y ampliación.
  El XML se muestra mediante texto escapado, sin interpretarlo como HTML.
- Conceptos permite filtrar por clave SAT, descripción, fecha, unidad, moneda,
  emisor/receptor, impuesto, categoría y documento. Las etiquetas de documentos
  se guardan como texto separado por comas, con búsqueda por coincidencia de texto.
  Una clave SAT no homologa productos: no se comparan precios unitarios
  ni se suman cantidades de unidades incompatibles automáticamente.
- En móvil se proponen columnas esenciales; el panel ocupa el área de detalle.
  La comprobación visual real queda pendiente de ejecutar Odoo.

## Diccionario de métricas

Universo común: alcance autorizado, dirección, filtros activos, base temporal
seleccionada y archivado/no archivado. Una compañía empieza seleccionada; las
perspectivas de varias compañías no constituyen consolidación con eliminaciones.
Cada barra entrega un dominio exacto a la lista del modelo fuente.

| Indicador | Fuente y fórmula | Signo / moneda / fecha / exclusiones |
|---|---|---|
| Volumen único | Conteo `som.add.document`, también por tipo y mes | Un UUID por compañía; ambos roles propios cuentan una vez; rechazados no son documentos; archivados se filtran aparte; fecha seleccionada |
| Facturación y ajustes | I y E separados; `SubTotal − Descuento` o `Total`, según selector | Importe documental positivo por tipo, moneda original, fecha seleccionada; excluye cancelados confirmados; P/T no forman facturación |
| Base neta opcional | `Σ base(I) − Σ base(E)` | Por moneda; no es utilidad contable; los ajustes se ubican en su propio periodo, no se trasladan al de su factura |
| Evolución mensual | Suma de la medida por tipo, moneda y mes | Fecha seleccionada; bordes del filtro pueden ser meses incompletos |
| Comparación de periodos | Mismas reglas en el intervalo inmediatamente anterior con igual número de días | Requiere desde/hasta; etiqueta las fechas comparadas; no anualiza ni completa meses |
| Concentración | Top 10 RFC de contraparte de I, importe / importe total filtrado, acumulado y Otros | Separada por moneda y dirección; disponible para una dirección; no mezcla I/E ni conceptos |
| Impuestos globales | Impuestos globales + locales, sin conceptos | Por moneda, tipo CFDI, traslado/retención y código; no significa impuestos a pagar/acreditable definitivo |
| Impuestos por tasa | Impuestos de conceptos por código, factor y tasa/cuota | Comprobación separada de globales; exento ≠ tasa cero ≠ no objeto; impuestos por línea conservan su concepto |
| PUE/PPD | Conteo y suma de medida por método, tipo I/E y moneda | Clasificación fiscal; no prueba pago bancario |
| Pagos documentados | `Σ Pago.Monto`, conteo de nodos Pago | **FechaPago y MonedaP**, no Fecha del encabezado ni Moneda XXX; no suma Totales de Pagos 2.0 otra vez |
| Aplicaciones | Conteo de aplicaciones; cargadas/faltantes según destino local | Mismo dominio de pagos; `ImpPagado` y saldos se consultan por MonedaDR, no se suman entre divisas ni se presentan como cartera actual |
| Conceptos | Top 20 grupos de importe por clave SAT, unidad y moneda de I comerciales | Excluye concepto técnico de P y E del ranking de facturas; no compara precios de productos no homologados |
| Calidad | Conteos de validación parcial, alertas, no consultados y relaciones ausentes | Mismo alcance documental; relaciones conservan UUID aunque no exista destino |
| Incidencias de lotes | Duplicados, conflictos, rechazados y fallidos | **Fecha de carga**, compañía/dirección; incluye entradas que no pueden tener fecha fiscal/moneda válidas; indicado como universo independiente |
| Comparación MXN opcional | Medida MXN original o medida original × `TipoCambio` del XML | Fuente visible; no usa TC actual. Cuenta documentos excluidos sin TC aplicable; no convierte impuestos/pagos ni representa póliza |

No hay indicadores de vinculación contable, por el alcance final solicitado.
SAT inicia **No consultado**. El filtro **Vigencia confirmada** solo incluye
evidencias registradas mediante el punto de extensión privado
`som.add.document._record_sat_result`. No existe botón SAT sin servicio real.
Un fallo de refresco conserva la última condición confirmada y registra su error.

### Precisión y fechas

El parser opera con `Decimal`. El JSON persistido y el XML conservan los valores
literales; campos `Float` indexados son proyecciones para las agregaciones ORM.
La tabla redondea según decimales ISO de la moneda; el detalle mantiene la
precisión literal. CSV/XLSX incluyen fuente y datos originales para reconstruir
los cálculos; XLSX distingue celdas numéricas de textos y no produce fórmulas.

La tolerancia del total es `max(0.02, 0.005 × (componentes globales/locales + 2))`:
dos centavos como mínimo y medio centavo por componente redondeado. La
comprobación de conceptos usa `max(0.02, 0.005 × partidas)`. Es un control
documental inicial para CFDI MXN/USD, no validación criptográfica/XSD ni dictamen
fiscal para todas las monedas. Complementos desconocidos implican **Validación
parcial**. No se corrigen importes del XML.

Fechas fiscales y de pago conservan su cadena original sin añadir UTC; se indexa
su fecha local. La carga/auditoría usa el timestamp estándar UTC del ORM.

## Versiones y límites

- CFDI 3.3/4.0: tipos I/E/P/T. Nómina N se detecta y rechaza con nombre genérico,
  sin guardar sus bytes, desglose ni datos personales.
- Pagos 1.0/2.0: nodos Pago, aplicaciones, impuestos P/DR y Totales 2.0 conservados.
- Impuestos federales por nivel y complemento de impuestos locales.
- Carta Porte: resumen de identificador, ubicaciones, transporte, mercancías,
  pedimentos/contenedores presentes. Probado sintéticamente con namespace 3.1;
  otras versiones detectadas conservan su árbol y los campos reconocidos, sin
  afirmar validación integral de su esquema. Números en descripción se etiquetan
  como referencias extraídas que requieren revisión.
- Addendas y otros complementos: árbol seguro íntegro y aviso de ausencia de
  desglose especializado. CFDI de retenciones e información de pagos: no soportado.
- No verifica certificados, sellos, vigencia SAT, deducibilidad o autenticidad
  criptográfica. Parseado consistente no significa fiscalmente vigente.

| Límite inicial | Valor | Ajuste |
|---|---:|---|
| XML | 5 MiB | 1–10 MiB por compañía |
| Archivo recibido XML/ZIP | 25 MiB | Hasta 50 MiB |
| Lote descomprimido | 100 MiB | Hasta 250 MiB |
| Entradas por ZIP / archivos por lote | 1,000 | Hasta 2,000 |
| Relación de compresión | 100:1 | Fijo |
| Profundidad XML | 48 | Fijo |
| Nodos XML | 60,000 | Fijo |
| Presupuesto del parser | 5 segundos por XML | Comprobado tras parseo y durante recorrido; lxml limita además tamaño/profundidad; no es un interruptor del sistema operativo |
| Bloque de persistencia | 20 | 1–100 |
| Pendientes/fallidos en lotes detenidos | 7 días | 1–90 días; limpieza programada |
| Exportación síncrona | 10,000 documentos / 100,000 filas de detalle | Reducir filtros al exceder |
| ZIP de originales | 100 MiB de originales | Reducir selección al exceder |

DTD, entidades, red y carga de esquemas están deshabilitados. No se extrae ZIP a
disco; se rechazan rutas peligrosas, enlaces simbólicos, cifrados y ZIP anidados.
No se envía XML a terceros ni se escribe en logs generales. Se conservan los
originales incorporados incluso cuando se archivan documentos.

## Evidencia de pruebas

Ejecutado desde `Módulos`:

```sh
python3 Ventas/account_statement_report/tests/test_add_offline.py -v
python3 Ventas/account_statement_report/tests/test_add_structure.py -v
node --check Ventas/account_statement_report/static/src/add/explorer.js
python3 Ventas/account_statement_report/tests/benchmark_add.py --count 1000
```

También: análisis AST de todos los Python del addon, parseo XML de vistas y
plantillas, lectura de XLSX generado mediante `openpyxl` para comprobar hojas,
celda monetaria numérica y texto de fórmula sin ejecutar. No se instalaron paquetes.

Resultados de fixtures **sintéticos**, sin presentar validación de los originales:

| Comprobación | Resultado |
|---|---:|
| Documentos | 8 (7 I + 1 P) |
| Conceptos comerciales | 12 |
| Facturas I MXN | 186,177.52 MXN |
| Factura I USD | 4,542.55 USD |
| Pago documentado | 17,777.00 MXN, FechaPago 4 jul 2025 |
| Carta Porte | 2 |
| Aplicación con UUID ausente en el conjunto | 1 |
| Misma entrada analítica repetida | Sin aumento del resumen por UUID |
| Carga con dirección emitida para el receptor de los fixtures | 8 rechazos por dirección |

Los tests offline incluyen también emisión, E, otras compañías, prefijos y
versiones, pagos múltiples, varias aplicaciones/USD, precisión, retenciones
por concepto, exento/cero/cuota/no objeto, XSS como texto, DTD/XXE UTF-16,
profundidad/tamaño/nodos/tiempo, ZIP Slip/enlaces/bomba y fórmulas CSV/XLSX.

La suite Odoo añade RPC/ACL, hijos/almacén privado, revocación, contadores,
idempotencia persistente, conflictos, relaciones tardías, fechas y ausencia de
efectos contables. **Pendiente ejecutar**; tampoco se ejecutaron pruebas HTTP
directas `/web/content`, portal, carrera de dos transacciones PostgreSQL,
compilación de assets ni recorrido visual escritorio/móvil. Estas comprobaciones
son necesarias antes de dar por verificada la instalación.

### Rendimiento medido

Medición del parser: macOS 26.6.1 ARM64, Python 3.9.6; 1,000 lecturas de ocho
fixtures sintéticos repetidos, **1,846,750 bytes** de entrada. Una ejecución con
`tracemalloc` registró **0.2701 s**, **3,702.05 XML/s**, pico Python **54,351 bytes**.
Ese pico no incluye toda la memoria nativa de lxml. No es una medición de lote
persistido ni de 1,000 documentos únicos en PostgreSQL.

Diseño: páginas de servidor, agregaciones ORM, índices en compañía/UUID/fechas/
RFC/tipo/moneda/lote, restricción única, bloques y creación agrupada de conceptos
e impuestos. **No se probó una base con 100,000 documentos** ni se afirma ese
rendimiento de extremo a extremo. Medir en la base de desarrollo con su hardware,
sus índices y el resto de módulos instalados antes de ajustar los límites.
