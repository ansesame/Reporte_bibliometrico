''' Constants of the bibliometric pipeline '''

MISSING = "Not indicated" # Missing element inside a multi-valued field

# OpenAlex
OA_BASE = "https://api.openalex.org/"
# Parallel requests: the rate is OA_RATE_SHARE of the per-second limit of the key (/rate-limit, 30/s for a free
# or prepaid key). The margin absorbs network jitter (OpenAlex counts arrivals, not departures) and other
# programs using the same key. OA_FALLBACK_RPS is the lowest limit (no key), used if /rate-limit cannot be read
OA_RATE_SHARE = 2 / 3
OA_FALLBACK_RPS = 10
OA_FALLBACK_PRICES = {'search': 0.001, 'list': 0.0001} # USD per request, if /rate-limit cannot be read
INST_TREE_LEVELS = 3 # Levels of the institution tree assumed by the imputation cost estimate
OA_WORKERS = 8 # Threads: requests are network bound; with ~0.4 s responses 8 threads reach the 20/s rate
TOPIC_FIELDS = ['id', 'display_name', 'description', 'legacy_keywords', 'ids', 'subfield', 'field', 'domain',
                'works_count', 'updated_date'] # select of the topics catalog (the full records weigh ~40 MB)
SUBFIELD_FIELDS = ['id', 'display_name', 'field'] # select of the subfields catalog

ALLOWED_TYPES = ["article", "report", "dissertation", "book", "book-chapter", "conference-paper",
                 "dataset", "data-paper"]
SEARCH_FIELD = "title-abstract" # OQL search field of the works query

INTEREST_VARS = ["id", "doi", "display_name", "relevance_score",
                 "publication_year", "publication_date", "language",
                 "primary_location.landing_page_url", "primary_location.source.host_organization_name",
                 "primary_location.source.display_name", "primary_location.raw_source_name",
                 "primary_location.source.is_oa", "primary_location.source.type", "type",
                 "open_access.is_oa", "open_access.oa_status", "open_access.oa_url",
                 "fwci", "cited_by_count", "citation_normalized_percentile.value",
                 "primary_topic.id", "primary_topic.display_name",
                 "primary_topic.subfield.id", "primary_topic.subfield.display_name",
                 "primary_topic.field.id", "primary_topic.field.display_name",
                 "primary_topic.domain.id", "primary_topic.domain.display_name",
                 "updated_date"]

AUTHORS_VARS = ["author.id", "author.display_name"] # Inside "authorships" list
INSTITUTIONS_VARS = ["id", "display_name", "country_code", "type"] # Inside "authorships[n].institutions" list
ABSTRACT_OBJECT = "abstract_inverted_index" # Abstract as words with their positions
KEYWORDS_OBJECT = "keywords" # Keyword objects with "id", "display_name" and "score"
SDG_OBJECT = "sustainable_development_goals" # SDG objects sorted by "score" (the first one is kept)

AUTHOR_COLS = ['authorships.author.id', 'authorships.author.display_name', 'authorships.author.id_imputed']
INST_COLS = [f'authorships.institutions.{inst_var}' for inst_var in INSTITUTIONS_VARS] + ['authorships.institutions.root_display_name']
INST_GROUP_COL = 'authorships.institutions.inst_group'
INST_TABLE_COLS = INST_COLS + [INST_GROUP_COL]

INST_SELECT = "id,display_name,display_name_alternatives,display_name_acronyms,country_code,type" # Institution search
INST_TREE_SELECT = "id,display_name,ror,country_code,type,associated_institutions,works_count" # Institution tree
AUTHORS_SELECT = "id,display_name,display_name_alternatives,affiliations,relevance_score" # Author search
AUTHOR_ENTITY_SELECT = "id,display_name,orcid,affiliations"
INSTITUTION_AUTHOR_TYPES = {'government', 'nonprofit', 'other', 'healthcare', 'facility', 'funder'} # Accepted as institutional authors

# Organization-like author names (utils.looks_like_org): only these names are searched as institutions.
# Global lists (Spanish, English, Portuguese, French and international/regional acronyms), not adjusted per
# country; the country terms come from the queries file. Normalized: no accents, lower case.
# Words that are also common in person names (cent*, central, who, un, can, fond*) are left out on purpose
ORG_PREFIXES = ( # A token matches if it starts with one of them
    # Universities and institutes
    'univers', 'institu', 'facult', 'academ', 'colleg', 'politecn', 'polytechn', 'tecnolog', 'technolog',
    # Government and State
    'minist', 'secretar', 'depart', 'gobiern', 'govern', 'gouvern', 'municip', 'prefectur', 'prefeitur',
    'superintend', 'contralor', 'procurad', 'asamble', 'assemble', 'congres', 'parlament', 'embaj', 'embass',
    'embaix', 'ambassad', 'consul',
    # Organizations
    'organi', 'fundac', 'foundation', 'fondation', 'associa', 'asocia', 'societ', 'socied', 'corpora', 'cooperat',
    'federa', 'confedera', 'consor', 'allianc', 'alianz',
    # Commissions and councils
    'comis', 'commis', 'comit', 'committ', 'consej', 'conselh', 'conseil', 'council', 'agenc',
    # Programs and services
    'program', 'proyect', 'project', 'projet', 'iniciativ', 'initiativ', 'servic', 'system', 'network', 'group',
    # Research and health
    'observator', 'laborator', 'hospital', 'clinic',
    # Companies and publishers
    'compan', 'compagn', 'industri', 'edicion', 'edicoe', 'edition', 'editor', 'publish', 'publicac',
)
ORG_WORDS = { # A token matches if it is equal to one of them
    'escuela', 'escola', 'ecole', 'school', 'colegio', 'liceo', 'lycee',
    'centro', 'centros', 'center', 'centers', 'centre', 'centres', 'nucleo', 'unidad', 'unidade', 'unite', 'unit',
    'oficina', 'office', 'bureau', 'escritorio',
    'banco', 'bank', 'banque', 'banca', 'fund', 'fondo', 'fonds', 'fundo',
    'red', 'rede', 'reseau', 'grupo', 'equipo', 'equipe', 'team', 'sistema', 'systeme',
    'tribunal', 'corte', 'court', 'cour', 'juzgado', 'fiscalia', 'defensoria', 'senado', 'senate', 'senat',
    'alcaldia', 'mairie', 'gad', 'junta', 'board', 'camara', 'chamber', 'chambre',
    'empresa', 'entreprise', 'enterprise', 'editora', 'press', 'verlag',
    'nacional', 'national', 'nationale', 'internacional', 'international', 'internationale',
    'naciones', 'nations', 'nacoes', 'unidas', 'unies', 'united', 'union', 'uniao',
}
ORG_ACRONYMS = { # Acronyms of frequent organizations, matched as whole tokens
    'cepal', 'eclac', 'oecd', 'ocde', 'fao', 'unesco', 'unicef', 'undp', 'pnud', 'onu', 'bid', 'idb', 'caf', 'imf',
    'fmi', 'oms', 'ops', 'paho', 'ilo', 'oit', 'wto', 'omc', 'unctad', 'usaid', 'giz', 'jica', 'iica', 'flacso',
    'clacso', 'ifad', 'fida', 'unep', 'pnuma', 'unhcr', 'acnur', 'iom', 'oim', 'wfp', 'pma', 'ifc', 'oea', 'oas',
    'sela', 'mercosur', 'aladi', 'celac', 'unasur', 'iucn', 'uicn', 'wwf', 'conicet', 'senescyt', 'inec', 'ibge',
    'insee',
}
ORG_LEGAL_PATTERN = r'\b(s a( s)?|sas|sarl|ltda|ltd|llc|inc|corp|gmbh|cia)\b' # Legal forms, on the normalized name

# Columns used to impute and then dropped from the studies output
DROP_COLS = ['primary_location.source.type',
             'primary_location.source.host_organization_name', 'primary_location.source.display_name',
             'authorships.author.id']
# Imputation trace columns: kept in the studies file, dropped from the database
SOURCE_COLS = ['institutions_source', 'publisher_source', 'language_imputed', 'abstract_source',
               'source_is_oa_imputed', 'authorships.author.id_imputed']

# Missing primary_location.source.is_oa from open_access.oa_status (green is ambiguous and is not imputed)
OA_STATUS_SOURCE_OA = {'gold': True, 'diamond': True, 'hybrid': False, 'bronze': False, 'closed': False}

# Abstracts, publisher and language
S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
EPMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
ROR_URL = "https://api.ror.org/v2/organizations/"
DOI_HEADERS = {"Accept": "application/vnd.citationstyles.csl+json",
               "User-Agent": "Reporte-bibliometrico-BID/1.0"}
DOMAIN_PUBLISHERS = {
    "documents.worldbank.org": "World Bank",
    "pesquisa.bvsalud.org": "Biblioteca Virtual en Salud (BVS)",
    "researchgate.net": "ResearchGate",
}
TEXT_COLUMNS = ['doi', 'language', 'abstract', 'primary_location.source.host_organization_name',
                'primary_location.source.display_name', 'primary_location.raw_source_name']
LANGUAGES = ["ENGLISH", "SPANISH", "PORTUGUESE", "FRENCH", "ITALIAN", "GERMAN"] # lingua Language names

# Institution classification with Claude
CLAUDE_MODEL = "claude-haiku-5-5"
CLASSIFY_BATCH_SIZE = 100
TOKENS_PER_INSTITUTION = 50
CLAUDE_PRICE_PER_MTOK = {'input': 0.1, 'output': 0.5} # USD per million tokens

THEMATIC_COLS = ['thematic_axis', 'pillar', 'strategic_objective'] # Added to the studies file
