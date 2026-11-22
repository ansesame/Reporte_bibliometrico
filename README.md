# Reporte bibliométrico con OpenAlex

Pipeline que, a partir de un archivo JSON con ejes temáticos y palabras clave, produce la base bibliométrica de un país:

1. **Consulta** OpenAlex con una consulta OQL por eje temático: estudios del país, entre los años definidos y con alguna de las palabras clave en el título o el abstract.
2. **Filtra** los estudios cuyo subcampo de OpenAlex es de interés.
3. **Elimina duplicados** en cascada por id, DOI y título.
4. **Imputa** datos faltantes:
   - Abstract
   - Idioma;
   - Si la fuente es de acceso abierto
   - El editor o quien administra el repositorio
   - Autores y instituciones.
5. **Clasifica** el origen de cada institución con Claude: el país, LAC, Europa, multilaterales, etc.
6. **Exporta** un archivo de estudios y una base relacional en Excel.

## Requisitos

- Python 3.12.
- Claves de API en un archivo `.env`.

| Variable | Uso | Obligatoria |
|---|---|---|
| `OPENALEX_API_KEY` | Consultas e imputación de autores e instituciones | Sí |
| `ANTHROPIC_API_KEY` | Clasificación del origen de las instituciones | Sí |
| `S2_API_KEY` | Abstracts desde Semantic Scholar. Sin clave se usa el límite compartido, que puede responder 429 y dejar sin recuperar esos abstracts | No |

Para obtener una API KEY de OpenAlex registrese en la página web y accesa a el espacio [API Key](https://openalex.org/settings/api-key) donde puede obtener su llave.

Para obtener una API Key de Anthropic debe tener una cuenta en Claude Platform e ingresar al panel [Claves y Acceso](https://platform.claude.com/settings/keys), donde podra crear una clave/key. También debe tener suficiente saldo para completar la ejecución, el detalle del costo lo encontrará más adelante.

## Uso con la interfaz

La interfaz de usuario instala el entorno, guarda las claves y ejecuta el pipeline sin usar la terminal. Solo requiere Python 3.12 o superior instalado:

Para abrirla, haz doble clic en:

| Sistema | Archivo | Nota |
|---|---|---|
| Windows | `Iniciar.bat` | Si no encuentra Python 3.12 con tkinter, explica qué instalar. El equipo puede bloquear la ejecución por se descargado de la web, ejecutelo como administrador.|
| macOS | `Iniciar.command` | La primera vez, macOS puede bloquearlo por venir de internet: clic derecho -> Abrir -> Abrir. Si dice que no tiene permiso de ejecución, corre `chmod +x Iniciar.command` en la Terminal, desde la carpeta del proyecto. Se abre una ventana de Terminal detrás de la interfaz; ciérrala al terminar. |

También se puede abrir desde python con `python scripts/app.py` .

La ventana tiene cuatro secciones:

1. **Entorno.** "Instalar entorno" crea `.venv` e instala `requirements.txt`. La primera vez tarda unos minutos. Después, la interfaz solo pide reinstalar si cambia `requirements.txt`, o si `.venv` se creó con otra versión de Python.
2. **Claves de API.** "Verificar y guardar claves" prueba cada clave con un endpoint gratuito y la guarda en `.env`.
3. **Parámetros.**
   - El JSON de queries y el Excel de subcampos. Al elegir el JSON, la interfaz muestra el país, los años y el número de ejes. Si el archivo tiene un error de formato, indica la línea y la columna.
   - El nombre (opcional).
   - Las casillas *Actualizar* (en caso de que exista una versión previa del reporte) y *Stemming* (uso de lematización en las palabras clave), activadas por defecto.

   La interfaz también avisa si ya existe una base con ese nombre (se actualizará) y si la corrida anterior quedó interrumpida (conviene desactivar *Actualizar* para retomarla).
4. **Consola.** Muestra los mensajes del pipeline mientras corre, con las barras de progreso. "Detener" interrumpe la corrida; los ejes ya consultados quedan respaldados. Al terminar, la consola se guarda en `outputs/{name}_backup/run_log_<fecha_hora>.txt` y el botón "Abrir resultados" abre `outputs/`.


## Uso con terminal

Preparar entorno de trabajo

En Windows:

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env_example .env
```

En Linux o macOS:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env_example .env
```

Después, completa las claves de API en archivo `.env`.

`requirements.txt` tiene solo las dependencias del pipeline. 

Ejecute el flujo de reporte usando 
```
.venv\Scripts\python.exe scripts\run_pipeline.py data\queries_<pais>.json --subfields data\subfields_tagged_<pais>.xlsx 
```

Aquí se asume que el json de queries y excel de filtro de subcampos están en la carpeta data/

En Linux o macOS se usa `.venv/bin/python scripts/run_pipeline.py ...`.

| Parámetro | Descripción |
|---|---|
| `queries` | Ruta del JSON de queries (obligatorio). |
| `--subfields` | Excel de subcampos de interés del país (obligatorio), p. ej. `data/subfields_tagged_honduras.xlsx`. |
| `--name` | Nombre base de las salidas. Por defecto es `bibliometrics_<country>`. |
| `--stemming` / `--no-stemming` | Busca las palabras clave con stemming (por defecto) o como frase exacta.|
| `--update` / `--no-update` | Por defecto, cada corrida es una actualización, vuelve a buscar todos los términos en OpenAlex y actualiza todos los datos. Por el contrario, `--no-update` reutiliza los respaldos de los ejes procesados para tener que pagar completo en caso de que algún query falle. Ver [Actualización y respaldos](#actualización-y-respaldos). |

Durante la ejecución se imprime el progreso de cada etapa.


## Replicar el reporte para un país nuevo

### 1. Crear el archivo de queries

Copia `data/queries_honduras.json` como `data/queries_<pais>.json` y edítalo. Tiene dos partes: `meta` y `thematics`.

**`meta`**

| Campo | Descripción |
|---|---|
| `country` | Nombre del país en inglés (p. ej. `"Guatemala"`). Se usa en el prompt de clasificación, como grupo de las instituciones nacionales y en el nombre de salida por defecto. |
| `start_year`, `end_year` | Rango de años de publicación, ambos incluidos. |
| `country_terms` | Términos que identifican al país: el nombre y los gentilicios en inglés y español, con sus variantes de género y número (`"Guatemalan"`, `"guatemalteco"`, `"guatemaltecas"`…). |
| `description` | Descripción libre del conjunto de queries (opcional). |

**`thematics`**: una entrada por eje temático.

| Campo | Descripción |
|---|---|
| `thematic_id` | Identificador único, como texto (`"111"`). |
| `pillar` | Pilar de la estrategia. |
| `strategic_objective` | Objetivo estratégico. |
| `expected_result` | Resultado esperado. |
| `thematic_axis` | Nombre corto del eje. |
| `keywords` | Lista de palabras clave en inglés y en el idioma local a buscar en el título y en el abstract  |

> **Generar el JSON con apoyo de IA.** El JSON se puede generar con cualquier chat de IA. Para Honduras, se le pasó a Claude (chat) el archivo `Reporte bibliometrico-Honduras-2019-2026.pdf` con la instrucción de llenar el JSON a partir de la tabla de la sección "2. Definición de palabras clave". Después se revisó manualmente. Un prompt de ejemplo se puede encontrar en [Ejemplo: replicación para Ecuador](#ejemplo-replicación-para-ecuador).

### 2. Revisar los subcampos de interés

Cada país tiene su archivo de subcampos, `data/subfields_tagged_<pais>.xlsx` debido a que la estrategia país cambia, que permite filtrar los estudios de interés. Este archivo con los 252 subcampos de OpenAlex tiene las variables:
- La columna `id` es el número del subcampo.
- La columna `interest_subfield` marca con `1` los que se conservan y con `0` los que se descartan.
- La columna `justification` que indica los ejes temáticos que sustentan el interés en el subcampo
- Junto a columnas de nombres y descripción.

> **Confirmar los subcampos con apoyo de IA.** Una vez definido el JSON, el archivo de subcampos también se puede confirmar con un chat de IA. Se le pasan el JSON y los subcampos con su descripción, y se retaguea si hace falta. Al final, el experto revisa el resultado. Un prompt de ejemplo se puede encontrar en [Ejemplo: replicación para Ecuador](#ejemplo-replicación-para-ecuador).

### 3. Revisar el saldo de OpenAlex

La API de OpenAlex tiene un presupuesto diario de USD 1, que se renueva a las 00:00 UTC. Para ver el saldo disponible, acceda a la sección [Usage](https://openalex.org/settings/usage) en su perfil de OpenAlex.

**Costo estimado de la imputación.** Antes de las búsquedas pagadas de autores e instituciones, el pipeline imprime un estimado del costo y el saldo de la clave, si no es suficiente el saldo el flujo saltará las imputaciones. Por ejemplo, para Ecuador:

```
[estimate] OpenAlex imputation: ~USD 1.15 (upper bound USD 1.19) | searches: 110 authors as institutions, 12 publishers, up to 1021 authors | up to 99 entity batches | ~1 min
[estimate] Already spent on author entities: USD 0.06 | OpenAlex balance: daily USD 1.00 + prepaid USD 2.56
Warning: the estimate exceeds the daily balance; the prepaid balance will be used
```

## Salidas

Todas se escriben en `outputs/`.

| Archivo | Contenido |
|---|---|
| `{name}.xlsx` | Una fila por estudio, con todas las variables, las columnas de trazabilidad de las imputaciones y los campos temáticos (`thematic_axis`, `pillar`, `strategic_objective`). |
| `{name}_db.xlsx` | Base relacional, con una hoja por tabla. La descripción completa de hojas y variables se encuentran en [docs\diccionario_bibliometrics_bd.xlsx](https://github.com/ansesame/Reporte_bibliometrico/blob/main/docs/diccionario_bibliometrics_bd.xlsx) |
| `{name}_backup/` | Respuestas de OpenAlex por eje (`thematic_<id>.json`), el catálogo de tópicos descargado de la API de OpenAlex (`topics_snapshot.json`) y `run_summary.json`, con los conteos, costos, tiempos y llamadas fallidas de cada etapa. |

Columnas de trazabilidad en `{name}.xlsx`:
- `abstract_source`, `publisher_source` e `institutions_source` indican de dónde viene cada valor.
- `language_imputed` marca los idiomas detectados automáticamente.
- `source_is_oa_imputed` marca los estudios donde `primary_location.source.is_oa` se imputó desde `open_access.oa_status`.
- `authorships.author.id_imputed` marca los ids de autor imputados.

### Actualización y respaldos

**Por defecto, cada corrida actualiza el país.** Borra los `thematic_<id>.json` de `{name}_backup/` y vuelve a consultar todos los ejes en OpenAlex.
- La base se reconstruye con los resultados vigentes. Los estudios nuevos entran con la fecha de la nueva, y los que ya estaban en `{name}_db.xlsx` conservan su `retrieved_date`.
- Un estudio que la consulta nueva ya no devuelve, porque OpenAlex lo reclasificó o lo retiró, sale de la base.
- La imputación de autores e instituciones y la clasificación con Claude se rehacen completas, con su costo.

**`--no-update` reutiliza los respaldos.** Si `thematic_<id>.json` existe y está completo, ese eje no se vuelve a consultar. Úsalo para:
- **retomar una corrida que falló:** los ejes que ya terminaron se leen de su respaldo y solo se consultan los que faltan;
- **actualizar solo algunos ejes:** borra sus archivos y corre con `--no-update`;
- **reproducir una corrida** a partir de su carpeta de respaldo.

**Catálogo de tópicos.** La base relacional toma el subcampo, el campo y el dominio de cada estudio del catálogo de tópicos. OpenAlex puede reasignar un tópico a otro subcampo, así que el catálogo debe ser de la misma fecha que los registros de los estudios, por eso se guarda `topics_snapshot.json`.

Cada respaldo guarda la OQL con que se generó (`meta.pipeline_oql`), de tal manera que se garantice usar la misma consulta en cada actualización de un mismo reporte.

> **Los resultados cambian según la fecha de ejecución.** OpenAlex actualiza de forma continua su índice de búsqueda, sus registros (citas, tópicos, autores, afiliaciones) y su catálogo de instituciones. Por eso, la misma consulta puede devolver resultados distintos en distintos días, y esa variación no depende del pipeline.

## Costos y tiempos de referencia

Honduras, 2019–2026, 20 ejes, 1.047 estudios finales (2026-11-20):

| Concepto | Costo |
|---|---|
| OpenAlex: catálogo de tópicos y control de subcampos | ~USD 0.003 |
| OpenAlex: consultas por eje | ~USD 0.03 |
| OpenAlex: imputación de autores e instituciones | ~USD 0.09 |
| Anthropic: clasificación de 812 instituciones con `claude-haiku-5-5` | ~USD 0.02 |
| **Total** | **~USD 0.14** |
| **Tiempo total** | **~3 minutos** |

El costo crece con el número de estudios y de autores sin datos. Si el total de OpenAlex supera el saldo diario se debe ejecutar usando saldo prepagado.

La comparación con Ecuador (16.033 estudios) y una estimación del costo por estudio están en [Ejemplo: replicación para Ecuador](#ejemplo-replicación-para-ecuador).

## Validaciones del flujo

- **Archivo de subcampos:** si sus ids no coinciden con los subcampos actuales de OpenAlex, el pipeline se detiene antes de consultar.
- **Consultas inválidas:** antes de consultar, las OQL se validan con un endpoint gratuito. Si alguna es inválida, el pipeline se detiene sin gastar.
- **Falla en un eje:** si la consulta de un query (eje temático) falla, el pipeline se detiene. Hay que volver a ejecutarlo **con `--no-update`**: los ejes que ya terminaron se leen de su respaldo y solo se consultan los que faltan. Sin ese parámetro, la corrida borraría los respaldos y consultaría todo de nuevo.
- **Falla en la imputación:** si una llamada falla, por ejemplo por falta de saldo, la imputación continúa sin ese dato. Las llamadas fallidas quedan registradas en `run_summary.json` y aparece un aviso en la consola.
- **Límite de solicitudes por segundo:** la imputación hace las consultas a OpenAlex en paralelo (`OA_WORKERS` = 8 hilos). Para no exceder el límite de la clave, al iniciar se lee ese límite y todas las solicitudes pasan por un limitador común que usa 2/3 de él. Con una clave gratuita o prepagada, el límite es de 30/s y la tasa queda en 20/s. 
- **Respuestas 429:** si OpenAlex rechaza una solicitud por el límite por segundo, se espera lo que indica `Retry-After` y se reintenta (hasta 10 veces), sin costo. Si la rechaza porque se agotó el saldo diario (`daily_budget`), no se reintenta y queda registrada como falla. Los demás errores 4xx, como un 400 por caracteres no válidos, tampoco se reintentan.

- **Tópicos:** el tópico, subcampo, campo y dominio de cada estudio se comparan con el catálogo. Puede haber diferencias en esta estructura si OpenAlex reasignó tópicos entre la fecha de los registros de los estudios y la del catálogo. El número de estudios con diferencias queda en `run_summary.json` (`stages.database.studies_with_topic_differences`) y, si es mayor que 0, aparece un aviso en la consola. Con el catálogo alineado debería ser 0. 

## Estructura del código

```
Iniciar.bat               abre la interfaz en Windows (doble clic)
Iniciar.command           abre la interfaz en macOS (doble clic)
data/                     queries por país y subcampos de interés
scripts/app.py            interfaz gráfica
scripts/run_pipeline.py   orquesta las etapas y escribe las salidas
src/bibliometrics/
  config.py               constantes: variables, tipos, selects, modelo
  utils.py                ids, listas unidas por ";" y normalización de nombres
  queries.py              lectura y validación del JSON, construcción de la OQL
  openalex.py             cliente de la API, catálogos de tópicos y subcampos
  works.py                resultados de OpenAlex -> tabla de estudios
  selection.py            filtro por subcampo y eliminación de duplicados
  enrichment.py           abstract, idioma y publisher
  authorships.py          imputación y estandarización de autores e instituciones
  classification.py       clasificación del origen de las instituciones con Claude
  database.py             archivo de estudios y tablas de la base relacional
  export.py               escritura de Excel y JSON
notebooks/test_oql.ipynb  pruebas y justificación de decisiones metodológica
outputs/                  salidas
```

`src/` solo contiene funciones reutilizables y no define el orden de ejecución; ese orden lo fija `scripts/`.

## Ejemplo: replicación para Ecuador

Ecuador se replicó a partir de `docs/Reporte bibliografico Ecuador 2019-2026.pdf`. La tabla de la sección "2. Definición de palabras clave" (págs. 4–10) tiene 3 áreas prioritarias, 5 objetivos estratégicos, 13 resultados esperados y 22 ejes temáticos, cada uno con sus palabras clave en inglés y español.

### Paso 1: `data/queries_ecuador.json`

La tabla se pasó al formato de `queries_honduras.json` con estas decisiones:

| Decisión | Detalle |
|---|---|
| `pillar` | El "Área prioritaria" del reporte (p. ej. `"1. Promoción de la inversión privada"`). |
| `thematic_id` | 4 dígitos: área · objetivo estratégico · resultado esperado · eje. Honduras usa 3 porque cada resultado tiene un solo eje; en Ecuador varios resultados tienen 2 o 3 ejes.|
| `country_terms` | `Ecuador`, los gentilicios en inglés y español (`Ecuadorian`, `ecuatoriano`, `ecuatorianas`…) y sus variantes `afroecuatoriano…`, igual que en Honduras. |
| Palabras clave | Cada término separado por `/` o `;` en la tabla es una palabra clave. Se quitaron los duplicados dentro de cada eje, incluidas las variantes de mayúsculas o singular/plural que el stemming ya iguala. |
| Notas del reporte | Se agregaron las variantes británicas `labour` en Regulación laboral, como indica la nota del reporte, y las "Other key words" de 3.1.1 quedaron en el mismo eje. |
| Erratas | Se corrigieron erratas evidentes del PDF (`digitalizacíon`, `subministro`, `evasion`…) y las tildes de los nombres de los ejes. |



### Paso 2: `data/subfields_tagged_ecuador.xlsx`

Se partió de una copia del archivo de Honduras. Se revisó `interest_subfield` contra los ejes de Ecuador y se reescribió `justification` con los ejes temáticos propios.


### Prompts de ejemplo para un chat de IA (ChatGPT, Claude, Gemini, etc)

**Prompt A: archivo de queries.** Adjunta el PDF/XLSX que contiene los ejes temáticos y palabras claves asociadas al reporte del país y `data/queries_honduras.json`.

```
Adjunto los pilares, objetivos estratégicos, ejes temáticos y palabras clave para el reporte bibliométrico de <PAÍS> en el documento <NOMBRE DOCUMENTO> (PDF/XLSX) y un archivo de queries de ejemplo (queries_honduras.json).
Genera queries_<pais>.json con exactamente el mismo formato, a partir de la tabla en <SECCIÓN EN DOCUMENTO O HOJA DEL EXCEL>

Reglas:
1. meta: country en inglés, start_year y end_year del período del reporte, una description breve, y country_terms con el nombre del país y sus gentilicios en inglés y español, con variantes de género (y étnias comunes si aplica).

2. Una entrada en thematics por cada eje temático de la tabla. pillar = área prioritaria (o pilar), strategic_objective = objetivo estratégico, expected_result = resultado esperado, thematic_axis = nombre del eje. 
Escribe los numerales con punto final ("1.1.", "1.1.1.").

3. thematic_id: texto que siga la jerarquía (pilar, objetivo, resultado y, si un    resultado tiene varios ejes, un dígito más para el eje). Todos deben ser únicos.

4. keywords: cada término separado por "/" o ";" en la tabla es un elemento de la lista, en inglés y español. 
Elimina duplicados dentro de cada eje (sin distinguir mayúsculas ni singular/plural).
Respeta las notas de la tabla y corrige erratas evidentes.

5. Si la tabla está en PDF y una celda continúa en la página siguiente, asigna el eje al resultado esperado que corresponde.

Devuelve el JSON completo y, aparte, una lista breve de las decisiones que tomaste (erratas corregidas, términos agregados o eliminados, asignaciones dudosas).

```

**Prompt B: subcampos de interés.** Adjunta el JSON generado y el Excel de subcampos de un país con ejes parecidos (p. ej. `data/subfields_tagged_ecuador.xlsx`).

```
Adjunto el archivo de queries de <PAÍS> queries_<pais>.json (ejes temáticos con thematic_id y palabras clave) y un Excel con los 252 subcampos de OpenAlex (id, name, description, field), etiquetados para otro país en la columna interest_subfield (1 = se conserva, 0 = se descarta).

Revisa cada subcampo para <PAÍS>: marca 1 si es probable que un estudio de ese subcampo trate alguno de los ejes temáticos del archivo de queries, y 0 si no. 

En justificacion, para cada 1 escribe los thematic_id y ejes que lo
justifican (p. ej. "2131 Agua potable y saneamiento") junto a una muy breve explicación; agrega "; dudoso" si la asignación es incierta.

Devuelve una tabla con las mismas columnas del excel de entrada para los 252 subcampos.
```

Los resultados deben revisarse antes de proceder.

### Paso 3: ejecución

```
.venv\Scripts\python.exe scripts\run_pipeline.py data\queries_ecuador.json --subfields data\subfields_tagged_ecuador.xlsx --name bibliometrics_ecuador
```

Resultado (2026-11-20): de 22.288 estudios recuperados en los 22 ejes, 18.749 pasaron el filtro de subcampos y quedaron 16.033 después de eliminar duplicados, con 2.341 instituciones clasificadas.


### Costos de referencia: Honduras vs Ecuador

| Concepto | Honduras | Ecuador |
|---|---|---|
| Ejes temáticos | 20 | 22 |
| Estudios recuperados / finales | 1.442 / 1.047 | 22.288 / 16.033 |
| OpenAlex: consultas por query | USD 0.03 | USD 0.23 |
| OpenAlex: imputación de autores e instituciones | USD 0.09 | USD 1.14 |
| OpenAlex: catálogo de tópicos y control de subcampos | USD 0.003 | USD 0.003 |
| Anthropic: clasificación de instituciones | USD 0.02 (812) | USD 0.06 (2.341) |
| **Total** | **USD 0.14** | **USD 1.43** |
| Costo por 1.000 estudios finales | USD 0.13 | USD 0.09 |
| Tiempo | ~3 minutos | ~25 minutos |
