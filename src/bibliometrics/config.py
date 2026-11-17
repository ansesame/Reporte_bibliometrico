''' Constants of the bibliometric pipeline '''

MISSING = "Not indicated" # Missing element inside a multi-valued field

# OpenAlex
OA_BASE = "https://api.openalex.org/"
TOPICS_MANIFEST = "https://openalex.s3.amazonaws.com/data/jsonl/topics/manifest.json" # Public snapshot: no API key or cost
TOPIC_FIELDS = ['id', 'display_name', 'description', 'legacy_keywords', 'ids', 'subfield', 'field', 'domain',
                'works_count', 'updated_date'] # Fields kept in the backup (the full records weigh ~40 MB)

ALLOWED_TYPES = ["article", "report", "conference-paper", "book", "book-chapter",
                 "dissertation", "conference-abstract"]

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
AUTHOR_ENTITY_SELECT = "id,display_name,affiliations"
INSTITUTION_AUTHOR_TYPES = {'government', 'nonprofit', 'other', 'healthcare', 'facility', 'funder'} # Accepted as institutional authors

# Columns used to impute and then dropped from the studies output
DROP_COLS = ['primary_location.source.type',
             'primary_location.source.host_organization_name', 'primary_location.source.display_name',
             'authorships.author.id']
# Imputation trace columns: kept in the studies file, dropped from the database
SOURCE_COLS = ['institutions_source', 'publisher_source', 'language_imputed', 'abstract_source',
               'source_is_oa_imputed']

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
