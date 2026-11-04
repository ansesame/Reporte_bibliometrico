''' Imputation and standardization of authors and institutions.

The ";" joined authorships columns are handled as aligned lists in Authorships: within each study,
every column of a group has the same number of elements, and a missing element is MISSING '''
import re
from collections import Counter
from dataclasses import dataclass, field

import pandas as pd
import requests

from .config import (MISSING, INSTITUTIONS_VARS, AUTHOR_COLS, INST_COLS, INST_SELECT, AUTHORS_SELECT,
                     AUTHOR_ENTITY_SELECT, INST_TREE_SELECT, INSTITUTION_AUTHOR_TYPES, ROR_URL)
from .utils import short_id, split_list, join_list, norm_name, name_similarity, group_counts


@dataclass
class Authorships:
    ''' Aligned author and institution lists per study (Series with the studies index) '''
    author_ids: pd.Series
    author_names: pd.Series
    id_imputed: pd.Series
    inst_lists: pd.Series # List of institution dicts (INSTITUTIONS_VARS) per study
    inst_sources: pd.Series # List of sources of the institutions per study
    author_entities: dict = field(default_factory=dict) # {author id: OpenAlex entity}
    org_matches: dict = field(default_factory=dict) # {author name: matched institution}
    root_names: dict = field(default_factory=dict) # {institution id: root institution name}
    stats: dict = field(default_factory=dict)

    def empty_studies(self):
        ''' Index of the studies without institutions '''
        return self.inst_lists[self.inst_lists.str.len() == 0].index


# Primitives

def inst_record(inst):
    ''' Institution fields used in the studies '''
    return {inst_var: inst.get(inst_var) for inst_var in INSTITUTIONS_VARS}


def match_institution(client, name, threshold=0.85):
    ''' OpenAlex institution matching a name (full name, then its first segment with 2+ words).
    The most similar of the first 10 results is kept: the exact match is not always ranked first
    (e.g. "United Nations" comes after FAO) and the cost of a search does not depend on per_page '''
    first_segment = re.split(r'[.,(]', name)[0].strip()
    queries = [name] + ([first_segment] if first_segment != name and len(first_segment.split()) >= 2 else [])
    for query in queries:
        data = client.get("institutions", {"search": query, "per_page": 10, "select": INST_SELECT})
        scored = [(name_similarity(query, [inst['display_name']] + (inst.get('display_name_alternatives') or [])
                                   + (inst.get('display_name_acronyms') or [])), inst)
                  for inst in (data or {}).get('results', [])]
        if scored:
            similarity, inst = max(scored, key=lambda item: item[0])
            if similarity >= threshold:
                return {**inst_record(inst), 'similarity': round(similarity, 2)}
    return None


def affiliation_ids(author, years):
    ''' Institution ids of the author affiliations in any of the years '''
    return {aff['institution']['id'] for aff in author.get('affiliations') or []
            if set(aff.get('years') or []) & years}


def search_author(client, name, year):
    ''' Author by name: same normalized name and an affiliation within ±1 year of the publication.
    Assigned when there is one valid candidate or all of them share an institution (duplicated profiles).
    Returns (author or None, number of valid candidates) '''
    data = client.get("authors", {"search": name, "per_page": 10, "select": AUTHORS_SELECT})
    years = {year - 1, year, year + 1}
    valid = []
    for author in (data or {}).get('results', []):
        author_names = [author['display_name']] + (author.get('display_name_alternatives') or [])
        same_name = norm_name(name) in {norm_name(author_name) for author_name in author_names}
        if same_name and affiliation_ids(author, years):
            valid.append(author)
    if not valid:
        return None, 0
    if len(valid) == 1 or set.intersection(*(affiliation_ids(author, years) for author in valid)):
        return max(valid, key=lambda author: author.get('relevance_score') or 0), len(valid)
    return None, len(valid)


def fetch_ror(ror, cache):
    ''' Name, country and type of an organization from the ROR API v2 ({} without ROR) '''
    if not ror:
        return {}
    if ror not in cache:
        try:
            req = requests.get(ROR_URL + short_id(ror), timeout=30)
            org = req.json() if req.status_code == 200 else {}
        except (requests.RequestException, ValueError):
            org = {}
        names = [name['value'] for name in org.get('names', []) if 'ror_display' in name.get('types', [])]
        location = (org.get('locations') or [{}])[0]
        cache[ror] = {'display_name': names[0] if names else None,
                      'country_code': (location.get('geonames_details') or {}).get('country_code'),
                      'type': (org.get('types') or [None])[0]}
    return cache[ror]


def fetch_institution_tree(client, ids):
    ''' OpenAlex entities of the institutions and all their parents ({institution id: entity}) '''
    institutions, requested, pending = {}, set(), set(ids)
    while pending: # Fetch parents (grandparents...) until no new ones are found
        requested = requested | pending
        institutions.update(client.fetch_entities('institutions', pending, INST_TREE_SELECT))
        pending = {parent['id'] for inst in institutions.values()
                   for parent in inst.get('associated_institutions') or []
                   if parent.get('relationship') == 'parent'} - requested
    return institutions


def root_institution(inst_id, institutions):
    ''' Id of the root parent institution of inst_id, going up the "parent" relations.
    With several parents it picks the one with the same type, then the one with most works.
    Without parents returns inst_id '''
    visited = {inst_id}
    for _ in range(len(institutions) + 1):
        inst = institutions.get(inst_id) or {}
        parents = [parent for parent in inst.get('associated_institutions') or []
                   if parent.get('relationship') == 'parent' and parent['id'] not in visited]
        if not parents:
            return inst_id
        inst_id = max(parents, key=lambda parent: (parent.get('type') == inst.get('type'),
                                                   (institutions.get(parent['id']) or {}).get('works_count') or 0))['id']
        visited.add(inst_id)
    return inst_id


# Stages

def study_institutions(row):
    ''' Institutions of a study as dicts; fields not aligned with the ids are left empty '''
    ids = split_list(row['authorships.institutions.id'])
    fields = {inst_var: split_list(row[f'authorships.institutions.{inst_var}']) for inst_var in INSTITUTIONS_VARS}
    fields = {inst_var: values if len(values) == len(ids) else [None] * len(ids) for inst_var, values in fields.items()}
    return [{inst_var: None if fields[inst_var][k] in ('', MISSING) else fields[inst_var][k] for inst_var in INSTITUTIONS_VARS}
            for k in range(len(ids))]


def prepare_authorships(client, studies):
    ''' Authors and institutions of the studies as aligned lists. Missing author ids become MISSING;
    names broken by a ";" are rebuilt from the OpenAlex author entities '''
    studies = studies.copy()
    for column in [column for column in AUTHOR_COLS + INST_COLS if column in studies.columns]:
        studies[column] = studies[column].where(studies[column].fillna('').astype(str).str.strip() != '')

    author_ids = studies['authorships.author.id'].map(split_list)
    author_names = studies['authorships.author.display_name'].map(split_list)
    author_entities = client.fetch_entities('authors', author_ids.explode().dropna(), AUTHOR_ENTITY_SELECT)

    # A ";" inside a name breaks the alignment: names are rebuilt from the author entities
    misaligned = (author_ids.str.len() > 0) & (author_ids.str.len() != author_names.str.len())
    author_names = author_names.mask(misaligned, author_ids.map(
        lambda ids: [(author_entities.get(author_id) or {}).get('display_name') for author_id in ids]))

    author_ids = pd.Series([[author_id or MISSING for author_id in ids] if ids else [MISSING] * len(names)
                            for ids, names in zip(author_ids, author_names)], index=studies.index)
    author_names = pd.Series([[name or (author_entities.get(author_id) or {}).get('display_name') or MISSING
                               for author_id, name in zip(ids, names)]
                              for ids, names in zip(author_ids, author_names)], index=studies.index)
    inst_lists = studies.apply(study_institutions, axis=1)
    auth = Authorships(author_ids=author_ids, author_names=author_names,
                       id_imputed=author_ids.map(lambda ids: [False] * len(ids)),
                       inst_lists=inst_lists, inst_sources=inst_lists.map(lambda insts: ['openalex'] if insts else []),
                       author_entities=author_entities)
    auth.stats.update({'author_entities': len(author_entities), 'names_rebuilt': int(misaligned.sum()),
                       'studies_without_institutions_openalex': len(auth.empty_studies())})
    return studies, auth


def add_authors_as_institutions(client, auth):
    ''' Authors that are institutions, only in studies without OpenAlex institutions: authors without id,
    or with a profile without affiliations in studies with <= 3 authors. A matched institution is added
    to every study where the name appears '''
    org_candidates = {name for index in auth.empty_studies()
                      for author_id, name in zip(auth.author_ids[index], auth.author_names[index])
                      if name != MISSING and (author_id == MISSING or
                                              (len(auth.author_names[index]) <= 3 and
                                               not (auth.author_entities.get(author_id) or {}).get('affiliations')))}
    auth.org_matches = {name: match for name in sorted(org_candidates) if (match := match_institution(client, name))}

    for index, names in auth.author_names.items():
        for name in names:
            match = auth.org_matches.get(name)
            if match and match['id'] not in {inst['id'] for inst in auth.inst_lists[index]}:
                auth.inst_lists[index].append(inst_record(match))
                if 'author_as_institution' not in auth.inst_sources[index]:
                    auth.inst_sources[index].append('author_as_institution')
    auth.stats.update({'org_candidates': len(org_candidates), 'org_matches': len(auth.org_matches),
                       'studies_author_as_institution': int(auth.inst_sources.map(lambda s: 'author_as_institution' in s).sum())})
    return auth


def add_publisher_as_authors(client, auth, studies):
    ''' In studies without authorships whose source is not a journal, the publisher is the institutional
    author if it matches an OpenAlex institution of an accepted type (not companies or universities) '''
    no_authors = auth.author_names[auth.author_names.str.len() == 0].index
    candidates = studies.loc[no_authors].query("`primary_location.source.type` != 'journal' and publisher.notna()")
    publisher_matches = {publisher: match_institution(client, publisher) for publisher in candidates['publisher'].unique()}

    for index, publisher in candidates['publisher'].items():
        match = publisher_matches[publisher]
        if match and match['type'] in INSTITUTION_AUTHOR_TYPES:
            auth.author_names[index].append(match['display_name'])
            auth.author_ids[index].append(MISSING) # Ids are not needed
            auth.id_imputed[index].append(False)
            auth.inst_lists[index].append(inst_record(match))
            auth.inst_sources[index].append('publisher_as_author')
    auth.stats.update({'studies_without_authors': len(no_authors), 'publisher_candidates': len(candidates),
                       'studies_publisher_as_author': int(auth.inst_sources.map(lambda s: 'publisher_as_author' in s).sum())})
    return auth


def impute_author_ids(client, auth, studies):
    ''' Author ids for person authors without id, only in studies still without institutions
    (the ids are used to recover their institutions). id_imputed flags the imputed positions '''
    empty_studies = auth.empty_studies()
    author_queries = {(name, int(studies.at[index, 'publication_year']))
                      for index in empty_studies
                      for author_id, name in zip(auth.author_ids[index], auth.author_names[index])
                      if author_id == MISSING and name != MISSING and name not in auth.org_matches}
    author_matches = {query: search_author(client, *query) for query in sorted(author_queries)}

    for index in empty_studies:
        ids, names = auth.author_ids[index], auth.author_names[index]
        year = int(studies.at[index, 'publication_year'])
        for k, (author_id, name) in enumerate(zip(ids, names)):
            author, _ = author_matches.get((name, year), (None, 0))
            if author_id == MISSING and author is not None:
                ids[k] = author['id']
                auth.id_imputed[index][k] = True
                auth.author_entities[author['id']] = author
    auth.stats.update({'searched_authors': len(author_queries),
                       'imputed_author_ids': sum(author is not None for author, _ in author_matches.values())})
    return auth


def add_author_affiliations(client, auth, studies):
    ''' Studies without institutions: institutions of their authors' affiliations in the publication year '''
    empty_studies = auth.empty_studies()
    missing_entities = {author_id for index in empty_studies for author_id in auth.author_ids[index]} - set(auth.author_entities)
    auth.author_entities.update(client.fetch_entities('authors', missing_entities, AUTHOR_ENTITY_SELECT))

    for index in empty_studies:
        year = int(studies.at[index, 'publication_year'])
        institutions = {}
        for author_id in auth.author_ids[index]:
            for aff in (auth.author_entities.get(author_id) or {}).get('affiliations') or []:
                if year in (aff.get('years') or []):
                    institutions.setdefault(aff['institution']['id'], inst_record(aff['institution']))
        if institutions:
            auth.inst_lists[index].extend(institutions.values())
            auth.inst_sources[index].append('author_affiliation')
    auth.stats.update({'studies_without_institutions': len(empty_studies),
                       'studies_without_institutions_after_affiliations': len(auth.empty_studies())})
    return auth


def standardize_institutions(client, auth):
    ''' 1. Missing fields (display_name, country_code, type): OpenAlex entity -> ROR.
    2. Ids with the same normalized name and country are merged into the most frequent id.
    3. Root parent institution name of each institution '''
    institutions_parents = fetch_institution_tree(client, {inst['id'] for insts in auth.inst_lists for inst in insts})

    fill_counts, ror_cache = Counter(), {}
    for insts in auth.inst_lists:
        for inst in insts:
            entity = institutions_parents.get(inst['id']) or {}
            for inst_var in ['display_name', 'country_code', 'type']:
                if inst.get(inst_var):
                    continue
                for source, get_value in [('openalex', lambda: entity.get(inst_var)),
                                          ('ror', lambda: fetch_ror(entity.get('ror'), ror_cache).get(inst_var))]:
                    if value := get_value():
                        inst[inst_var] = value
                        fill_counts[f'{inst_var}_{source}'] += 1
                        break

    # Canonical id: same normalized name and country -> most frequent id
    id_counts = Counter(inst['id'] for insts in auth.inst_lists for inst in insts)
    unique_insts = {inst['id']: inst for insts in auth.inst_lists for inst in insts}
    canonical, canonical_by_id = {}, {}
    for inst_id, inst in sorted(unique_insts.items(), key=lambda item: -id_counts[item[0]]):
        key = (norm_name(inst['display_name']), inst['country_code']) if inst['display_name'] else inst_id
        canonical_by_id[inst_id] = canonical.setdefault(key, inst)
    merged = {inst_id: inst['id'] for inst_id, inst in canonical_by_id.items() if inst_id != inst['id']}
    auth.inst_lists = auth.inst_lists.map(
        lambda insts: list({canonical_by_id[inst['id']]['id']: canonical_by_id[inst['id']] for inst in insts}.values()))

    auth.root_names = {inst_id: (institutions_parents.get(root_institution(inst_id, institutions_parents)) or {}).get('display_name')
                       or inst['display_name']
                       for inst_id, inst in unique_insts.items()}
    auth.stats.update({'institution_entities': len(institutions_parents), 'filled_fields': dict(fill_counts),
                       'merged_ids': len(merged)})
    return auth


def write_authorship_columns(studies, auth):
    ''' Aligned lists back to ";" joined columns, with alignment checks '''
    studies = studies.copy()
    studies['authorships.author.id'] = auth.author_ids.map(join_list)
    studies['authorships.author.display_name'] = auth.author_names.map(join_list)
    studies['authorships.author.id_imputed'] = auth.id_imputed.map(join_list)
    for inst_var in INSTITUTIONS_VARS:
        studies[f'authorships.institutions.{inst_var}'] = auth.inst_lists.map(lambda insts: join_list([inst[inst_var] for inst in insts]))
    studies['authorships.institutions.root_display_name'] = auth.inst_lists.map(
        lambda insts: join_list([auth.root_names[inst['id']] for inst in insts]))
    studies['institutions_source'] = auth.inst_sources.map(lambda sources: ';'.join(sources) or None)

    for group in (AUTHOR_COLS, INST_COLS):
        assert (group_counts(studies, group).nunique(axis=1) == 1).all(), f"Misaligned columns: {group}"
        assert not studies[group].map(lambda value: '' in split_list(value)).any().any(), f"Empty elements: {group}"
    inst_names = pd.DataFrame([(inst['id'], inst['display_name']) for insts in auth.inst_lists for inst in insts],
                              columns=['id', 'display_name'])
    assert (inst_names.groupby('id')['display_name'].nunique() == 1).all(), "Institution ids with more than one name"
    return studies
