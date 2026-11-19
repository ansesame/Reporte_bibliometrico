# Reporte bibliométrico con OpenAlex

Pipeline que, a partir de un archivo JSON con ejes temáticos y palabras clave, produce la base bibliométrica de un país:

1. **Consulta** OpenAlex con una consulta OQL por eje temático: estudios del país, entre los años definidos y con alguna de las palabras clave en el título o el abstract.
2. **Filtra** los estudios cuyo subcampo de OpenAlex es de interés.
3. **Elimina duplicados** en cascada por id, DOI y título.
4. **Imputa** datos faltantes:
   - el abstract, desde Semantic Scholar y Europe PMC;
   - el idioma;
   - si la fuente es de acceso abierto, a partir del estado de acceso abierto del estudio;
   - el publisher;
   - los autores y las instituciones.
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

Instalación en Windows:

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

Después, completa las claves en `.env`.

## Replicar el reporte para un país nuevo

### 1. Crear el archivo de queries

Copia `data/queries_honduras_ET.json` como `data/queries_<pais>.json` y edítalo. Tiene dos partes: `meta` y `thematics`.

**`meta`**

| Campo | Descripción |
|---|---|
| `country` | Nombre del país en inglés (p. ej. `"Guatemala"`). Se usa en el prompt de clasificación, como grupo de las instituciones nacionales y en el nombre de salida por defecto. |
| `start_year`, `end_year` | Rango de años de publicación, ambos incluidos. |
| `country_terms` | Términos que identifican al país: el nombre y los gentilicios en inglés y español, con sus variantes de género y número (`"Guatemalan"`, `"guatemalteco"`, `"guatemaltecas"`…). Un término de una sola palabra se busca tal cual; uno de varias palabras se busca con stemming (también con `--no-stemming`). |
| `description` | Descripción libre del conjunto de queries (opcional). |

**`thematics`**: una entrada por eje temático.

| Campo | Descripción |
|---|---|
| `thematic_id` | Identificador único, como texto (`"111"`). |
| `pillar` | Pilar de la estrategia. |
| `strategic_objective` | Objetivo estratégico. |
| `expected_result` | Resultado esperado. |
| `thematic_axis` | Nombre corto del eje. |
| `keywords` | Lista de palabras clave en inglés y en el idioma local. Se buscan en el título y el abstract, con stemming por defecto (encuentra variantes como plurales); con `--no-stemming` se buscan como frase exacta. |

> **Generar el JSON con apoyo de IA.** El JSON se puede generar con cualquier chat de IA. Para Honduras, se le pasó a Claude (chat) el archivo `Reporte bibliometrico-Honduras-2019-2026.pdf` con la instrucción de llenar el JSON a partir de la tabla de la sección "2. Definición de palabras clave". Después se revisó manualmente.

### 2. Revisar los subcampos de interés

Cada país tiene su archivo de subcampos, `data/subfields_tagged_<pais>.xlsx` (p. ej. `subfields_tagged_honduras.xlsx`), con los 252 subcampos de OpenAlex:
- La columna `id` es el número del subcampo.
- La columna `interest_subfield` marca con `1` los que se conservan y con `0` los que se descartan.
- Las columnas `name` y `field` (nombre y campo) se usan para avisar si OpenAlex los cambió.

Si los ejes temáticos del nuevo país son similares a los de otro país, parte de una copia de su archivo y retaguéala. El archivo se pasa siempre con `--subfields`.

Al iniciar, el pipeline compara el archivo con los subcampos actuales de OpenAlex (~USD 0.0002). Si OpenAlex tiene subcampos que el archivo no tiene, o al revés, se detiene antes de consultar, porque hay que etiquetar los subcampos nuevos. Si solo cambió el nombre o el campo de un subcampo, muestra un aviso y continúa; para quitar el aviso, actualiza el nombre en el archivo. El resultado queda en `run_summary.json` (`subfields_check`). Los archivos de Honduras y Ecuador tienen los nombres y las descripciones de la API de OpenAlex al 2026-11-19.

> **Confirmar los subcampos con apoyo de IA.** Una vez definido el JSON, el archivo de subcampos también se puede confirmar con un chat de IA. Se le pasan el JSON y los subcampos con su descripción, y se retaguea si hace falta. Al final, el experto revisa el resultado.

### 3. Revisar el saldo de OpenAlex

La API de OpenAlex tiene un presupuesto diario de USD 1, que se renueva a las 00:00 UTC. Para ver el saldo disponible, acceda a la sección [Usage](https://openalex.org/settings/usage) en su perfil de OpenAlex.

### 4. Ejecutar

```
.venv\Scripts\python.exe scripts\run_pipeline.py data\queries_<pais>.json --subfields data\subfields_tagged_<pais>.xlsx --name bibliometrics_<pais>
```

En Linux o macOS se usa `.venv/bin/python scripts/run_pipeline.py ...`.

| Parámetro | Descripción |
|---|---|
| `queries` | Ruta del JSON de queries (obligatorio). |
| `--name` | Nombre base de las salidas. Por defecto es `bibliometrics_<country>`. |
| `--subfields` | Excel de subcampos de interés del país (obligatorio), p. ej. `data/subfields_tagged_honduras.xlsx`. |
| `--stemming` / `--no-stemming` | Busca las palabras clave con stemming (por defecto) o como frase exacta. No cambia la búsqueda de los `country_terms`. |

Se buscan estos tipos de documento: `article`, `report`, `dissertation`, `book`, `book-chapter`, `conference-paper`, `dataset` y `data-paper` (`ALLOWED_TYPES` en `src/bibliometrics/config.py`).

Durante la ejecución, cada etapa imprime sus conteos (`[queries]`, `[duplicates]`, `[abstracts]`…). Las etapas que hacen muchas consultas (abstracts, idioma, publisher, autores e instituciones, clasificación con Claude) muestran una barra de progreso con el avance y el tiempo restante estimado.

## Salidas

Todas se escriben en `outputs/`.

| Archivo | Contenido |
|---|---|
| `{name}.xlsx` | Una fila por estudio, con todas las variables, las columnas de trazabilidad de las imputaciones y los campos temáticos (`thematic_axis`, `pillar`, `strategic_objective`). |
| `{name}_db.xlsx` | Base relacional, con una hoja por tabla (ver abajo). |
| `{name}_backup/` | Respuestas de OpenAlex por eje (`thematic_<id>.json`), el catálogo de tópicos descargado de la API de OpenAlex (`topics_snapshot.json`) y `run_summary.json`, con los conteos, costos, tiempos y llamadas fallidas de cada etapa. |

Columnas de trazabilidad en `{name}.xlsx`:
- `abstract_source`, `publisher_source` e `institutions_source` indican de dónde viene cada valor.
- `language_imputed` marca los idiomas detectados automáticamente.
- `source_is_oa_imputed` marca los estudios donde `primary_location.source.is_oa` se imputó desde `open_access.oa_status`.
- `authorships.author.id_imputed` marca los ids de autor imputados. Esta columna solo está en `{name}.xlsx`, no en la hoja `studies` de `{name}_db.xlsx`.

Fechas de cada estudio (en ambos archivos):
- `retrieved_date` es la fecha de la corrida en que el estudio entró a la base. Si ya existe `{name}_db.xlsx`, los estudios que ya estaban conservan su fecha y solo los nuevos reciben la fecha de la corrida actual.
- `openalex_updated_date` es la fecha en que OpenAlex actualizó el registro por última vez. No es la fecha de la corrida.

Hojas de `{name}_db.xlsx`:

| Hoja | Llave | Contenido |
|---|---|---|
| `studies` | `study_id` | Metadatos, abstract, publisher, ODS principal, autores y citaciones del estudio. |
| `institutions` | `institution_id` | Nombre, país, tipo, organización raíz y grupo de origen. |
| `study_institutions` | `study_id`, `institution_id` | Relación entre estudios e instituciones. |
| `topics` | `topic_id` | Catálogo completo de tópicos de OpenAlex, con su subcampo, campo y dominio. |
| `study_topics` | `study_id`, `topic_id` | Relación entre estudios y tópicos. |
| `thematics` | `thematic_id` | Ejes temáticos del JSON, con su OQL. |
| `study_thematics` | `study_id`, `thematic_id` | Relación entre estudios y ejes temáticos. |
| `study_year_cites` | `study_id`, `year` | Citas recibidas por año (`cited_by_count`), tomadas de `counts_by_year` de OpenAlex. Solo incluye los años que OpenAlex reporta: años con al menos una cita, y como máximo los últimos 10. |
| `metadata` | — | Una fila con el objeto `meta` del JSON de queries, los parámetros de búsqueda (`search_fields`, `allowed_types`, `stemming`) y `updated_date`, la fecha de la última actualización de los datos. Las listas, como `country_terms` y `allowed_types`, van unidas con `; `. |

Los ids se guardan sin el prefijo `https://openalex.org/`. Los ids de tópicos, subcampos, campos y dominios se guardan como número.

La descripción de cada hoja y columna, con su tipo, llave, origen, campo de OpenAlex y valores posibles, está en `docs/diccionario_bibliometrics_bd.xlsx`.

**Reutilización de respaldos.** Si `thematic_<id>.json` ya existe en la carpeta de respaldo, se reutiliza y el eje no se vuelve a consultar. Para actualizar un eje, borra su archivo. Para una corrida completamente nueva, usa otro `--name` o borra la carpeta.

**Catálogo de tópicos.** La base relacional toma el subcampo, el campo y el dominio de cada estudio del catálogo de tópicos. OpenAlex puede reasignar un tópico a otro subcampo, así que el catálogo debe ser de la misma fecha que los registros de los estudios. Por eso `topics_snapshot.json` se descarga de la API (~USD 0.002) o se reutiliza según cómo se obtuvieron los ejes:

| Situación de la corrida | `topics_snapshot.json` |
|---|---|
| Al menos un eje se consultó a OpenAlex (primera corrida, o se borró algún `thematic_<id>.json`) | Se descarga de nuevo de la API |
| Todos los ejes salen de respaldo y el catálogo existente vino de la API (`meta.source = "openalex_api"`) | Se reutiliza, sin costo; así una reejecución da el mismo resultado |
| Todos los ejes salen de respaldo y el catálogo existente no tiene `meta.source` (versión anterior, tomada del snapshot de S3) | Se descarga de la API |
| No existe | Se descarga de la API |

Para llevar todos los datos a la versión vigente de OpenAlex hay que borrar los `thematic_<id>.json`: con respaldos antiguos, el catálogo reutilizado tiene la misma antigüedad que los estudios.

Cada respaldo guarda la OQL con que se generó (`meta.pipeline_oql`). Si la OQL actual es distinta, porque cambiaron los tipos, el campo de búsqueda, las palabras clave o el stemming, el pipeline se detiene antes de consultar e indica qué respaldos borrar. Los respaldos creados antes de este control no tienen `pipeline_oql` y también detienen la corrida.

> **Los resultados cambian según la fecha de ejecución.** OpenAlex actualiza de forma continua su índice de búsqueda, sus registros (citas, tópicos, autores, afiliaciones) y su catálogo de instituciones. Por eso, la misma consulta puede devolver resultados distintos en distintos días, y esa variación no depende del pipeline ni se puede controlar desde aquí.

> Para que un reporte sea reproducible:
> - registra la fecha de extracción, que queda en `run_summary.json` y en los respaldos;
> - conserva la carpeta `{name}_backup/`. Al volver a ejecutar con esos respaldos se obtienen los mismos estudios, sin pagar otra vez las consultas.

## Costos y tiempos de referencia

Honduras, 2019–2026, 20 ejes, 1.044 estudios finales (2026-11-19):

| Concepto | Costo |
|---|---|
| OpenAlex: consultas por eje | ~USD 0.03 |
| OpenAlex: imputación de autores e instituciones | ~USD 0.32 |
| Anthropic: clasificación de 806 instituciones con `claude-haiku-5-5` | ~USD 0.02 |
| OpenAlex: catálogo de tópicos y control de subcampos | ~USD 0.003 |
| **Tiempo total** | **~15 minutos** |

El costo crece con el número de estudios y de autores sin datos. Si el total de OpenAlex supera el saldo diario se debe ejecutar el pipeline otro día o usar saldo prepagado.

## Validaciones y fallas

- **Archivo de subcampos:** si sus ids no coinciden con los subcampos actuales de OpenAlex, el pipeline se detiene antes de consultar.
- **Consultas inválidas:** antes de consultar, las OQL se validan con un endpoint gratuito. Si alguna es inválida, el pipeline se detiene sin gastar.
- **Falla en un eje:** si la consulta de un eje falla, el pipeline se detiene. Basta con volver a ejecutarlo: los ejes que ya terminaron se leen de su respaldo.
- **Falla en la imputación:** si una llamada falla, por ejemplo por falta de saldo, la imputación continúa sin ese dato. Las llamadas fallidas quedan registradas en `run_summary.json` y aparece un aviso en la consola.
- **Controles de consistencia:**
  - **Alineación:** en cada estudio, las columnas de autores tienen el mismo número de elementos, y lo mismo ocurre con las de instituciones.
  - **Llaves:** son únicas y no hay referencias rotas entre tablas.
  - **Tópicos:** el tópico, subcampo, campo y dominio de cada estudio se comparan con el catálogo. No detiene la corrida: el número de estudios con diferencias queda en `run_summary.json` (`stages.database.studies_with_topic_differences`) y, si es mayor que 0, aparece un aviso en la consola. Con el catálogo alineado (ver **Catálogo de tópicos**) debería ser 0. Puede haber diferencias si OpenAlex reasignó tópicos entre la fecha de los registros de los estudios y la del catálogo.

## Adaptaciones para países fuera de América Latina

El pipeline supone un país de América Latina y el Caribe. Para otras regiones, revisa:

| Qué | Dónde |
|---|---|
| Grupos de origen de las instituciones (`LAC`, `USA & Canada`, `Europe`…) y el prompt de clasificación | `inst_groups` y `system_prompt` en `src/bibliometrics/classification.py` |
| Idiomas que se pueden detectar (inglés, español, portugués, francés, italiano y alemán) | `LANGUAGES` en `src/bibliometrics/config.py` |
| Tipos de documento incluidos | `ALLOWED_TYPES` en `src/bibliometrics/config.py` |
| Publishers asignados por dominio web | `DOMAIN_PUBLISHERS` en `src/bibliometrics/config.py` |

## Estructura del código

```
data/                     queries por país y subcampos de interés
scripts/run_pipeline.py   orquesta las etapas y escribe las salidas
src/bibliometrics/
  config.py               constantes: variables, tipos, selects, modelo, columnas a eliminar
  utils.py                ids, listas unidas por ";" y normalización de nombres
  queries.py              lectura y validación del JSON, construcción de la OQL
  openalex.py             cliente de la API (costo y fallas), catálogos de tópicos y subcampos
  works.py                resultados de OpenAlex -> tabla de estudios
  selection.py            filtro por subcampo y eliminación de duplicados
  enrichment.py           abstract, idioma y publisher
  authorships.py          imputación y estandarización de autores e instituciones
  classification.py       clasificación del origen de las instituciones con Claude
  database.py             archivo de estudios y tablas de la base relacional
  export.py               escritura de Excel y JSON
notebooks/test_oql.ipynb  desarrollo y justificación de cada decisión metodológica
outputs/                  salidas
```

`src/` solo contiene funciones reutilizables y no define el orden de ejecución; ese orden lo fija `scripts/`.
