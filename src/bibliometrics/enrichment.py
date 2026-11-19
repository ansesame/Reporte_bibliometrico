''' Imputation of abstracts, language, source open access and publisher '''
import re
import time
from functools import lru_cache
from html import unescape

import pandas as pd
import requests
from tqdm import tqdm

from .config import (TEXT_COLUMNS, S2_BATCH_URL, EPMC_SEARCH_URL, DOI_HEADERS, DOMAIN_PUBLISHERS, LANGUAGES,
                     OA_STATUS_SOURCE_OA)
from .utils import clean_name, progress, progress_map


def blank_to_nan(studies, columns=TEXT_COLUMNS):
    ''' Empty texts as NaN in the text columns '''
    studies = studies.copy()
    studies[columns] = studies[columns].apply(lambda column: column.where(column.str.strip() != ''))
    return studies


# Abstracts

def fetch_s2_abstracts(dois, api_key=None, retries=10):
    ''' Abstracts from the Semantic Scholar batch endpoint by DOI (max 100 ids per request) '''
    headers = {"x-api-key": api_key} if api_key else {}
    abstracts = {}
    for start in progress(range(0, len(dois), 100), "Abstracts Semantic Scholar (batches)"):
        batch = dois[start:start + 100]
        for attempt in range(retries):
            req = requests.post(S2_BATCH_URL, params={"fields": "abstract"},
                                json={"ids": [f"DOI:{doi}" for doi in batch]}, headers=headers, timeout=120)
            if req.status_code != 429:
                break
            time.sleep(5) # Shared rate limit without API key
        if req.status_code != 200:
            tqdm.write(f"Semantic Scholar error: {req.status_code}")
            continue
        abstracts.update({doi: paper['abstract'] for doi, paper in zip(batch, req.json())
                          if paper and paper.get('abstract')})
    return abstracts


def fetch_epmc_abstract(doi):
    ''' Abstract from Europe PMC by DOI, without HTML tags (None if not found) '''
    try:
        req = requests.get(EPMC_SEARCH_URL, params={"query": f'DOI:"{doi}"', "resultType": "core", "format": "json"},
                           timeout=60)
    except requests.RequestException:
        return None
    if req.status_code != 200:
        return None
    results = req.json().get('resultList', {}).get('result', [])
    if not results or not results[0].get('abstractText'):
        return None
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', ' ', results[0]['abstractText']))).strip()


def impute_abstracts(studies, s2_api_key=None):
    ''' Missing abstracts by DOI: Semantic Scholar, then Europe PMC. abstract_source keeps the origin '''
    studies = studies.copy()
    studies['abstract_source'] = pd.Series('openalex', index=studies.index).where(studies['abstract'].notna())

    missing_abstract = studies['abstract'].isna() & studies['doi'].notna()
    dois = studies.loc[missing_abstract, 'doi'].str.removeprefix('https://doi.org/')

    s2_abstracts = dois.map(fetch_s2_abstracts(dois.tolist(), s2_api_key)).dropna()
    studies.loc[s2_abstracts.index, 'abstract'] = s2_abstracts
    studies.loc[s2_abstracts.index, 'abstract_source'] = 'semantic_scholar'

    epmc_abstracts = progress_map(dois.drop(s2_abstracts.index), fetch_epmc_abstract, "Abstracts Europe PMC").dropna()
    studies.loc[epmc_abstracts.index, 'abstract'] = epmc_abstracts
    studies.loc[epmc_abstracts.index, 'abstract_source'] = 'europepmc'
    return studies


# Language

@lru_cache(maxsize=1)
def language_detector():
    ''' lingua detector restricted to the expected languages (built once) '''
    from lingua import Language, LanguageDetectorBuilder
    return LanguageDetectorBuilder.from_languages(*(getattr(Language, name) for name in LANGUAGES)).build()


def detect_language(*texts):
    ''' ISO 639-1 code of the first text with a detectable language (None if none) '''
    for text in texts:
        if isinstance(text, str) and text.strip():
            language = language_detector().detect_language_of(text)
            if language is not None:
                return language.iso_code_639_1.name.lower()
    return None


def impute_language(studies):
    ''' Missing language detected from the title, then the abstract. language_imputed flags the imputed rows '''
    studies = studies.copy()
    studies['language_imputed'] = studies['language'].isna()
    imputed = studies['language_imputed']
    if imputed.any():
        rows = studies.loc[imputed, ['display_name', 'abstract']]
        studies.loc[imputed, 'language'] = [detect_language(title, abstract) for title, abstract
                                            in progress(rows.itertuples(index=False), "Language", total=len(rows))]
    return studies


# Source open access

def impute_source_oa(studies):
    ''' Missing primary_location.source.is_oa (unknown source) from open_access.oa_status:
    gold and diamond imply a fully OA source; hybrid, bronze and closed a non OA one; green stays missing.
    source_is_oa_imputed flags the imputed rows '''
    studies = studies.copy()
    column = 'primary_location.source.is_oa'
    imputed = studies[column].isna() & studies['open_access.oa_status'].isin(list(OA_STATUS_SOURCE_OA))
    studies.loc[imputed, column] = studies.loc[imputed, 'open_access.oa_status'].map(OA_STATUS_SOURCE_OA)
    studies['source_is_oa_imputed'] = imputed
    return studies


# Publisher

def doi_publisher(doi):
    ''' Publisher registered for the DOI (Crossref or DataCite) via doi.org content negotiation '''
    try:
        req = requests.get(doi, headers=DOI_HEADERS, timeout=30)
        publisher = req.json().get('publisher') if req.status_code == 200 else None
    except (requests.RequestException, ValueError) as e:
        tqdm.write(f"Warning: {doi} ({type(e).__name__})")
        return None
    if req.status_code != 200:
        tqdm.write(f"Warning: {doi} ({req.status_code})")
    return publisher if publisher != "Unpublished" else None # ResearchGate DOIs are registered as "Unpublished"


def landing_publisher(url):
    ''' Publisher from the landing page domain; doi.org links are resolved to their final domain '''
    if not isinstance(url, str):
        return None
    if re.match(r'https?://(dx\.)?doi\.org/', url):
        try:
            url = requests.head(url, allow_redirects=True, timeout=30).url
        except requests.RequestException:
            pass
    domain = re.sub(r'^https?://(www\.)?', '', url).split('/')[0]
    return DOMAIN_PUBLISHERS.get(domain, domain)


PUBLISHER_STEPS = {
    'host_organization_name': lambda df: df['primary_location.source.host_organization_name'],
    'display_name': lambda df: df['primary_location.source.display_name'],
    'raw_source_name': lambda df: df['primary_location.raw_source_name'],
    'doi': lambda df: progress_map(df['doi'].dropna(), doi_publisher, "Publisher by DOI"),
    'landing_page_url': lambda df: progress_map(df['primary_location.landing_page_url'], landing_publisher,
                                                "Publisher by landing page"),
}


def impute_publisher(studies):
    ''' publisher by cascade (first step with a value); publisher_source keeps the step '''
    studies = studies.copy()
    studies['publisher'] = None
    studies['publisher_source'] = None
    for step, get_names in PUBLISHER_STEPS.items():
        names = get_names(studies[studies['publisher'].isna()]).map(clean_name).dropna()
        studies.loc[names.index, 'publisher'] = names
        studies.loc[names.index, 'publisher_source'] = step
    studies['publisher'] = studies['publisher'].str.replace('eBooks', '').str.strip()
    return studies
