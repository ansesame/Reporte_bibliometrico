# Reporte bibliométrico con OpenAlex

Pipeline que, a partir de un archivo JSON con ejes temáticos y palabras clave, produce la base bibliométrica de un país:

1. **Consulta** OpenAlex con una consulta OQL por eje temático: estudios del país, entre los años definidos y con alguna de las palabras clave en el título, el abstract o las keywords.
2. **Filtra** los estudios cuyo subcampo de OpenAlex es de interés.
3. **Elimina duplicados** en cascada por id, DOI y título.
4. **Imputa** datos faltantes:
   - el abstract, desde Semantic Scholar y Europe PMC;
   - el idioma;
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
| `country_terms` | Términos que identifican al país: el nombre y los gentilicios en inglés y español, con sus variantes de género y número (`"Guatemalan"`, `"guatemalteco"`, `"guatemaltecas"`…). Un término de una sola palabra se busca tal cual; uno de varias palabras se busca con stemming. |
| `description` | Descripción libre del conjunto de queries (opcional). |

**`thematics`**: una entrada por eje temático.

| Campo | Descripción |
|---|---|
| `thematic_id` | Identificador único, como texto (`"111"`). |
| `pillar` | Pilar de la estrategia. |
| `strategic_objective` | Objetivo estratégico. |
| `expected_result` | Resultado esperado. |
| `thematic_axis` | Nombre corto del eje. |
| `keywords` | Lista de palabras clave en inglés y en el idioma local. |

> **Generar el JSON con apoyo de IA.** El JSON se puede generar con cualquier chat de IA. Para Honduras, se le pasó a Claude (chat) el archivo `Reporte bibliometrico-Honduras-2019-2026.pdf` con la instrucción de llenar el JSON a partir de la tabla de la sección "2. Definición de palabras clave". Después se revisó manualmente.

### 2. Revisar los subcampos de interés

`data/subfields_tagged.xlsx` lista los 252 subcampos de OpenAlex:
- La columna `id` es el número del subcampo.
- La columna `interest_subfield` marca con `1` los que se conservan y con `0` los que se descartan.

Si los ejes temáticos del nuevo país son similares a los de Honduras, el archivo se puede reutilizar. Si cambian, crea una copia retagueada y pásala con `--subfields`.

> **Confirmar los subcampos con apoyo de IA.** Una vez definido el JSON, `subfields_tagged.xlsx` también se puede confirmar con un chat de IA. Se le pasan el JSON y los subcampos con su descripción, y se retaguea si hace falta. Al final, el experto revisa el resultado.

### 3. Revisar el saldo de OpenAlex

La API de OpenAlex tiene un presupuesto diario de USD 1, que se renueva a las 00:00 UTC. Para ver el saldo disponible, acceda a la sección [Usage](https://openalex.org/settings/usage) en su perfil de OpenAlex.

### 4. Ejecutar

```
.venv\Scripts\python.exe scripts\run_pipeline.py data\queries_<pais>.json --name bibliometrics_<pais>
```

En Linux o macOS se usa `.venv/bin/python scripts/run_pipeline.py ...`.

| Parámetro | Descripción |
|---|---|
| `queries` | Ruta del JSON de queries (obligatorio). |
| `--name` | Nombre base de las salidas. Por defecto es `bibliometrics_<country>`. |
| `--subfields` | Excel de subcampos de interés. Por defecto es `data/subfields_tagged.xlsx`. |

## Salidas

Todas se escriben en `outputs/`.

| Archivo | Contenido |
|---|---|
| `{name}.xlsx` | Una fila por estudio, con todas las variables, las columnas de trazabilidad de las imputaciones y los campos temáticos (`thematic_axis`, `pillar`, `strategic_objective`). |
| `{name}_db.xlsx` | Base relacional, con una hoja por tabla (ver abajo). |
| `{name}_backup/` | Respuestas de OpenAlex por eje (`thematic_<id>.json`), el catálogo de tópicos (`topics_snapshot.json`) y `run_summary.json`, con los conteos, costos, tiempos y llamadas fallidas de cada etapa. |

Columnas de trazabilidad en `{name}.xlsx`:
- `abstract_source`, `publisher_source` e `institutions_source` indican de dónde viene cada valor.
- `language_imputed` marca los idiomas detectados automáticamente.
- `authorships.author.id_imputed` marca los ids de autor imputados.

Hojas de `{name}_db.xlsx`:

| Hoja | Llave | Contenido |
|---|---|---|
| `studies` | `study_id` | Metadatos, abstract, publisher, autores y citaciones del estudio. |
| `institutions` | `institution_id` | Nombre, país, tipo, organización raíz y grupo de origen. |
| `study_institutions` | `study_id`, `institution_id` | Relación entre estudios e instituciones. |
| `topics` | `topic_id` | Catálogo completo de tópicos de OpenAlex, con su subcampo, campo y dominio. |
| `study_topics` | `study_id`, `topic_id` | Relación entre estudios y tópicos. |
| `thematics` | `thematic_id` | Ejes temáticos del JSON, con su OQL. |
| `study_thematics` | `study_id`, `thematic_id` | Relación entre estudios y ejes temáticos. |

Los ids se guardan sin el prefijo `https://openalex.org/`. Los ids de tópicos, subcampos, campos y dominios se guardan como número.

**Reutilización de respaldos.** Si `thematic_<id>.json` o `topics_snapshot.json` ya existen en la carpeta de respaldo, se reutilizan y no se vuelven a consultar. Para actualizar un eje, borra su archivo. Para una corrida completamente nueva, usa otro `--name` o borra la carpeta.

> **Los resultados cambian según la fecha de ejecución.** OpenAlex actualiza de forma continua su índice de búsqueda, sus registros (citas, tópicos, autores, afiliaciones) y su catálogo de instituciones. Por eso, la misma consulta puede devolver resultados distintos en distintos días, y esa variación no depende del pipeline ni se puede controlar desde aquí.

> Para que un reporte sea reproducible:
> - registra la fecha de extracción, que queda en `run_summary.json` y en los respaldos;
> - conserva la carpeta `{name}_backup/`. Al volver a ejecutar con esos respaldos se obtienen los mismos estudios, sin pagar otra vez las consultas.

## Costos y tiempos de referencia

Honduras, 2019–2026, 20 ejes, 1.260 estudios finales (2026-11-03):

| Concepto | Costo |
|---|---|
| OpenAlex: consultas por eje | ~USD 0.03 |
| OpenAlex: imputación de autores e instituciones | ~USD 0.40 |
| Anthropic: clasificación de 838 instituciones con `claude-haiku-5-5` | ~USD 0.02 |
| Catálogo de tópicos, que se descarga del snapshot público de OpenAlex en S3 | USD 0 |
| **Tiempo total** | **~8 minutos** |

El costo crece con el número de estudios y de autores sin datos. Si el total de OpenAlex supera el saldo diario se debe ejecutar el pipeline otro día o usar saldo prepagado.

## Validaciones y fallas

- **Consultas inválidas:** antes de consultar, las OQL se validan con un endpoint gratuito. Si alguna es inválida, el pipeline se detiene sin gastar.
- **Falla en un eje:** si la consulta de un eje falla, el pipeline se detiene. Basta con volver a ejecutarlo: los ejes que ya terminaron se leen de su respaldo.
- **Falla en la imputación:** si una llamada falla, por ejemplo por falta de saldo, la imputación continúa sin ese dato. Las llamadas fallidas quedan registradas en `run_summary.json` y aparece un aviso en la consola.
- **Controles de consistencia:**
  - **Alineación:** en cada estudio, las columnas de autores tienen el mismo número de elementos, y lo mismo ocurre con las de instituciones.
  - **Llaves:** son únicas y no hay referencias rotas entre tablas.
  - **Tópicos:** el tópico de cada estudio coincide con el catálogo.

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
  openalex.py             cliente de la API (costo y fallas) y snapshot de tópicos
  works.py                resultados de OpenAlex -> tabla de estudios
  selection.py            filtro por subcampo y eliminación de duplicados
  enrichment.py           abstract, idioma y publisher
  authorships.py          imputación y estandarización de autores e instituciones
  classification.py       clasificación del origen de las instituciones con Claude
  database.py             archivo de estudios y tablas de la base relacional
  export.py               escritura de Excel y JSON
notebooks/test_oql.ipynb  desarrollo y justificación de cada decisión metodológica
outputs/                  salidas (no se versionan)
```

`src/` solo contiene funciones reutilizables y no define el orden de ejecución; ese orden lo fija `scripts/`.
