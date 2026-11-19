''' Queries file (thematic axes with keywords) and OQL construction '''
import json

import pandas as pd

from .config import ALLOWED_TYPES, SEARCH_FIELD

META_FIELDS = ['country', 'start_year', 'end_year', 'country_terms']
THEMATIC_FIELDS = ['thematic_id', 'pillar', 'strategic_objective', 'expected_result', 'thematic_axis', 'keywords']


def load_queries(path):
    ''' Queries file as dict {meta, thematics}, checking the required fields '''
    with open(path, "r", encoding="utf-8") as file:
        queries = json.load(file)
    missing_meta = [field for field in META_FIELDS if field not in queries.get('meta', {})]
    assert not missing_meta, f"Missing meta fields in {path}: {missing_meta}"
    assert queries.get('thematics'), f"No thematics in {path}"
    for thematic in queries['thematics']:
        missing = [field for field in THEMATIC_FIELDS if field not in thematic]
        assert not missing, f"Thematic {thematic.get('thematic_id')} without fields: {missing}"
        assert thematic['keywords'], f"Thematic {thematic['thematic_id']} without keywords"
    ids = [str(thematic['thematic_id']) for thematic in queries['thematics']]
    assert len(ids) == len(set(ids)), "Duplicated thematic ids"
    return queries


def build_oql(keywords, start_year, end_year, country_terms, allowed_types=ALLOWED_TYPES, field=SEARCH_FIELD,
              stem=True):
    ''' Build OQL: works between years, with allowed types, containing any country term AND any keyword
    in the title or abstract. Keywords are stemmed if stem, exact phrases otherwise; country terms of one word
    are searched as they are and those of several words are always stemmed '''
    clean = lambda text: text.replace('"', '').strip()
    kw_search = " OR ".join(f'stemmed "{clean(kw)}"' if stem else f'"{clean(kw)}"' for kw in keywords)
    country_search = " OR ".join(clean(term) if " " not in clean(term) else f'stemmed "{clean(term)}"'
                                 for term in country_terms)
    types = " or ".join(allowed_types)
    return (f"get works where year >= ({start_year}) and year <= ({end_year})"
            f" and type is ({types})"
            f" and {field} has (({country_search}) AND ({kw_search}))")


def thematic_oql(thematic, meta, stem=True):
    ''' OQL of a thematic axis with the meta of the queries file '''
    return build_oql(thematic['keywords'], meta['start_year'], meta['end_year'], meta['country_terms'], stem=stem)


def build_thematics_df(queries, stem=True):
    ''' Thematics dataframe: fields of the queries file, number of keywords and OQL '''
    thematics = pd.DataFrame(queries['thematics'])
    thematics['n_keywords'] = thematics['keywords'].apply(len)
    thematics['oql'] = [thematic_oql(thematic, queries['meta'], stem) for thematic in queries['thematics']]
    thematics['keywords'] = thematics['keywords'].apply('; '.join)
    return thematics
