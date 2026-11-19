''' Helpers for ids, ";" joined lists and names '''
import difflib
import re
import unicodedata

import pandas as pd
from tqdm import tqdm

from .config import MISSING


def progress(iterable, desc, total=None):
    ''' Progress bar over a slow loop (requests to APIs) '''
    return tqdm(iterable, desc=desc, total=total, dynamic_ncols=True)


def progress_map(series, func, desc):
    ''' series.map(func) with a progress bar, keeping the index '''
    return pd.Series([func(value) for value in progress(series, desc, total=len(series))],
                     index=series.index, dtype=object)


def short_id(openalex_id):
    ''' https://openalex.org/W123 -> W123 '''
    return openalex_id.rsplit('/', 1)[-1]


def num_id(openalex_id):
    ''' T10795 -> 10795, .../subfields/1800 -> 1800 '''
    return int(str(openalex_id).rsplit('/', 1)[-1].lstrip('T'))


def clean_value(value):
    ''' Text of a value without ";" (it would break the alignment of joined lists) '''
    return str(value or '').replace(';', ',')


def split_list(value):
    ''' ";" joined text as list ([] for NaN or empty text) '''
    return value.split(';') if isinstance(value, str) and value.strip() else []


def join_list(values):
    ''' List as ";" joined text, MISSING for empty elements (None for empty lists) '''
    if not values:
        return None
    return ';'.join(MISSING if value is None or value == '' else str(value).replace(';', ',') for value in values)


def clean_name(name):
    ''' Collapse whitespace; None for empty or non-text values '''
    if not isinstance(name, str):
        return None
    return re.sub(r'\s+', ' ', name).strip() or None


def norm_name(text):
    ''' Name without accents, case, punctuation or extra spaces '''
    text = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().casefold()
    return ' '.join(re.sub(r'[.,\-‐]', ' ', text).split())


def name_similarity(name, candidates):
    ''' Highest similarity between the normalized name and the candidates '''
    similarities = (difflib.SequenceMatcher(None, norm_name(name), norm_name(candidate)).ratio()
                    for candidate in candidates if candidate)
    return max(similarities, default=0)


def group_counts(df, columns):
    ''' Number of elements of each column of a group, per study '''
    return pd.concat([df[column].map(lambda value: len(split_list(value))) for column in columns], axis=1)
