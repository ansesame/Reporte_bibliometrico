''' Output tables: studies file with thematic fields and relational database tables '''
import pandas as pd

from .config import INST_COLS, INST_TABLE_COLS, SOURCE_COLS, MISSING, THEMATIC_COLS
from .utils import short_id, num_id, split_list

TOPIC_PAIRS = {'primary_topic.display_name': 'topic_name',
               'primary_topic.subfield.id': 'subfield_id', 'primary_topic.subfield.display_name': 'subfield_name',
               'primary_topic.field.id': 'field_id', 'primary_topic.field.display_name': 'field_name',
               'primary_topic.domain.id': 'domain_id', 'primary_topic.domain.display_name': 'domain_name'}


def build_audit_table(studies, thematics):
    ''' Studies with the thematic axis, pillar and strategic objective of their thematic ids (";" joined, sorted) '''
    audit = studies.copy()
    by_id = thematics.set_index('thematic_id')
    for column in THEMATIC_COLS:
        audit[column] = audit['thematic_id'].map(
            lambda value: ";".join(sorted({by_id.at[item, column] for item in split_list(value)})) if value else None)
    return audit


def build_studies_table(studies):
    ''' Studies table: entities move to their own tables, imputation trace columns are dropped '''
    topic_cols = [column for column in studies.columns if column.startswith('primary_topic.')]
    table = studies.drop(columns=['thematic_id'] + topic_cols + INST_TABLE_COLS + SOURCE_COLS).\
        rename(columns={'id': 'study_id'})
    table['study_id'] = table['study_id'].map(short_id)
    assert table['study_id'].is_unique, "Duplicated study ids"
    return table


def build_institution_tables(studies, study_ids):
    ''' (institutions, study_institutions): one row per institution id and one per study and institution '''
    study_institutions = pd.DataFrame(
        [(study_id, short_id(inst_id)) for study_id, inst_ids in zip(study_ids, studies['authorships.institutions.id'])
         for inst_id in split_list(inst_ids)],
        columns=['study_id', 'institution_id']).drop_duplicates(ignore_index=True)
    assert not study_institutions['institution_id'].eq(MISSING).any(), "Institutions without id"

    inst_rows = pd.DataFrame(
        [values for _, row in studies[INST_TABLE_COLS].iterrows() for values in zip(*map(split_list, row))],
        columns=[column.rsplit('.', 1)[-1] for column in INST_TABLE_COLS]).rename(columns={'id': 'institution_id'})
    inst_rows['institution_id'] = inst_rows['institution_id'].map(short_id)
    assert (inst_rows.groupby('institution_id').nunique() == 1).all().all(), "Institution ids with more than one value"
    institutions = inst_rows.drop_duplicates('institution_id', ignore_index=True)
    assert set(institutions['institution_id']) == set(study_institutions['institution_id'])
    return institutions, study_institutions


def build_thematic_tables(studies, study_ids, thematics):
    ''' (thematics, study_thematics): "210;211" becomes two bridge rows '''
    study_thematics = pd.DataFrame(
        {'study_id': study_ids, 'thematic_id': studies['thematic_id'].astype(str).str.split(';')}).\
        explode('thematic_id').drop_duplicates(ignore_index=True)
    assert study_thematics['thematic_id'].isin(thematics['thematic_id']).all(), "Unknown thematic ids"
    return thematics.copy(), study_thematics


def build_topics_table(topics_snapshot):
    ''' Topics catalog with the columns of the OpenAlex topic mapping table '''
    topics = pd.DataFrame([{
        'topic_id': num_id(topic['id']),
        'topic_name': topic['display_name'],
        **{f'{level}_{field}': (num_id(topic[level]['id']) if field == 'id' else topic[level]['display_name'])
           for level in ['subfield', 'field', 'domain']
           for field in ['id', 'name']},
        'keywords': topic.get('legacy_keywords'),
        'summary': topic.get('description'),
        'wikipedia_url': (topic.get('ids') or {}).get('wikipedia'),
        'works_count': topic.get('works_count'), 'openalex_updated_date': topic.get('updated_date'),
        } for topic in topics_snapshot['results']]).sort_values('topic_id', ignore_index=True)
    assert topics['topic_id'].is_unique, "Duplicated topic ids"
    return topics


def build_study_topics(studies, study_ids, topics):
    ''' Bridge study - primary topic '''
    study_topics = pd.DataFrame({'study_id': study_ids, 'topic_id': studies['primary_topic.id'].map(num_id)})
    assert study_topics['topic_id'].isin(topics['topic_id']).all(), "Study topics not in the catalog"
    return study_topics


def check_topic_assignments(studies, study_topics, topics):
    ''' Differences between the primary_topic.* columns of the studies and the catalog (one bool column per pair) '''
    topic_check = studies[list(TOPIC_PAIRS)].reset_index(drop=True).assign(
        topic_id=study_topics['topic_id'].to_numpy()).merge(topics, on='topic_id', how='left')
    return pd.DataFrame({column: (topic_check[column].map(num_id) if column.endswith('.id') else topic_check[column]).ne(topic_check[name])
                         for column, name in TOPIC_PAIRS.items()})


def build_study_year_cites(study_ids, counts_by_year):
    ''' Citations per year of each study as reported by OpenAlex (only the years in counts_by_year).
    counts_by_year: {short study id: list of {"year", "cited_by_count"}} '''
    missing = set(study_ids) - set(counts_by_year)
    assert not missing, f"Studies without counts_by_year: {sorted(missing)[:5]}"
    return pd.DataFrame(
        [(study_id, count['year'], count['cited_by_count'])
         for study_id in study_ids for count in counts_by_year[study_id]],
        columns=['study_id', 'year', 'cited_by_count']).sort_values(['study_id', 'year'], ignore_index=True)


def build_metadata_table(meta, run_date):
    ''' One row with the meta of the queries file (lists joined by "; ") and the date of the run '''
    return pd.DataFrame([{**{key: '; '.join(map(str, value)) if isinstance(value, list) else value
                             for key, value in meta.items()},
                          'updated_date': run_date}])


def assign_retrieved_date(studies, previous_db_path, run_date):
    ''' retrieved_date column: the date of the previous database for the studies already in it, run_date for the new ones.
    Returns (studies, number of studies that kept their previous date) '''
    previous = {}
    if previous_db_path.exists():
        previous_studies = pd.read_excel(previous_db_path, sheet_name='studies', dtype=str)
        if 'retrieved_date' in previous_studies.columns:
            previous = previous_studies.dropna(subset=['retrieved_date']).\
                set_index('study_id')['retrieved_date'].to_dict()
    studies = studies.copy()
    kept = studies['id'].map(short_id).map(previous)
    studies['retrieved_date'] = kept.fillna(run_date)
    return studies, int(kept.notna().sum())


def build_db_tables(studies, thematics, topics_snapshot, meta, run_date, counts_by_year):
    ''' Relational tables of the studies. Returns (tables, topic differences) '''
    studies = studies.reset_index(drop=True)
    studies_table = build_studies_table(studies)
    study_ids = studies_table['study_id']
    institutions, study_institutions = build_institution_tables(studies, study_ids)
    thematics_table, study_thematics = build_thematic_tables(studies, study_ids, thematics)
    topics = build_topics_table(topics_snapshot)
    study_topics = build_study_topics(studies, study_ids, topics)
    topic_diffs = check_topic_assignments(studies, study_topics, topics)
    tables = {'studies': studies_table, 'institutions': institutions, 'study_institutions': study_institutions,
              'topics': topics, 'study_topics': study_topics,
              'thematics': thematics_table, 'study_thematics': study_thematics,
              'study_year_cites': build_study_year_cites(study_ids, counts_by_year),
              'metadata': build_metadata_table(meta, run_date)}
    return tables, topic_diffs
