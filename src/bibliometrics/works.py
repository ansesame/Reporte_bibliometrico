''' OpenAlex works (JSON results) to a studies dataframe '''
import pandas as pd

from .config import INTEREST_VARS, AUTHORS_VARS, INSTITUTIONS_VARS, ABSTRACT_OBJECT, KEYWORDS_OBJECT, SDG_OBJECT
from .utils import clean_value


def build_abstract(abstract_inverted_index):
    ''' Abstract text from the inverted index (None if there is no index) '''
    if not isinstance(abstract_inverted_index, dict):
        return None
    index_expand = {i: word for word, positions in abstract_inverted_index.items() for i in positions}
    abstract = ' '.join(word for _, word in sorted(index_expand.items(), key=lambda x: x[0]))
    return ''.join(ch for ch in abstract if ch.isprintable() or ch.isspace()) # Printable chars only


def build_authors(authorships):
    ''' Authors fields joined by ";" keeping the authorship order '''
    authorships = authorships if isinstance(authorships, list) else []
    authors = [authorship.get('author') or {} for authorship in authorships]
    return {
        f'authorships.{au_var}': ';'.join(clean_value(author.get(au_var.split('.', 1)[1])) for author in authors)
        for au_var in AUTHORS_VARS
    }


def build_institutions(authorships):
    ''' Unique institutions (by id, keeping order) with aligned fields joined by ";" '''
    authorships = authorships if isinstance(authorships, list) else []
    institutions = {}
    for authorship in authorships:
        for inst in authorship.get('institutions') or []:
            institutions.setdefault(inst.get('id'), inst)
    return {
        f'authorships.institutions.{inst_var}': ';'.join(clean_value(inst.get(inst_var)) for inst in institutions.values())
        for inst_var in INSTITUTIONS_VARS
    }


def build_keywords(keywords):
    ''' Keywords display names joined by ";" '''
    keywords = keywords if isinstance(keywords, list) else []
    return ';'.join(keyword.get('display_name') or '' for keyword in keywords)


def build_sdg(sdgs):
    ''' Display name of the SDG with the highest score (OpenAlex sorts them by score), None if there is none '''
    return sdgs[0].get('display_name') if isinstance(sdgs, list) and sdgs else None


def build_studies_df(results, thematic_id):
    ''' Studies dataframe with interest variables, authors, institutions, abstract, keywords and main SDG '''
    extra_vars = ([f'authorships.{au_var}' for au_var in AUTHORS_VARS]
                  + [f'authorships.institutions.{inst_var}' for inst_var in INSTITUTIONS_VARS]
                  + ['abstract', 'keywords', f'{SDG_OBJECT}.display_name'])

    # Nested objects are built apart (json_normalize would expand the abstract index into one column per word)
    nested_vars = ['authorships', ABSTRACT_OBJECT, KEYWORDS_OBJECT, SDG_OBJECT]
    studies = pd.json_normalize([{k: v for k, v in result.items() if k not in nested_vars} for result in results])
    studies = studies.reindex(columns=INTEREST_VARS) # Missing columns as NaN

    extra = pd.DataFrame([
        {**build_authors(result.get('authorships')),
         **build_institutions(result.get('authorships')),
         'abstract': build_abstract(result.get(ABSTRACT_OBJECT)),
         'keywords': build_keywords(result.get(KEYWORDS_OBJECT)),
         f'{SDG_OBJECT}.display_name': build_sdg(result.get(SDG_OBJECT))}
        for result in results
    ], columns=extra_vars)

    studies = pd.concat([studies, extra], axis=1)
    studies['thematic_id'] = thematic_id
    return studies
