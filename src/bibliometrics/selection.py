''' Selection of studies: subfields of interest and duplicates '''
import pandas as pd


def filter_interest_subfields(studies, subfields):
    ''' Studies whose primary subfield is tagged with interest_subfield == 1.
    subfields: table with the OpenAlex subfield "id" and "interest_subfield".
    Studies without subfield are dropped (OpenAlex could not classify them).
    primary_topic.subfield.id becomes the short id as text ("1800") '''
    studies = studies.copy()
    studies['primary_topic.subfield.id'] = studies['primary_topic.subfield.id'].str.split("/").str[-1].astype(str)
    subfields = subfields.rename(columns={"id": "primary_topic.subfield.id"})
    subfields['primary_topic.subfield.id'] = subfields['primary_topic.subfield.id'].astype(str)
    studies = pd.merge(studies, subfields[['primary_topic.subfield.id', 'interest_subfield']],
                       how="left", on="primary_topic.subfield.id")
    return studies.query("interest_subfield == 1").drop(columns=["interest_subfield"])


def title_key(titles, min_words=4):
    ''' Normalized title as duplicate key, NaN for generic titles with less than min_words '''
    titles = titles.str.casefold().str.replace(r'\s+', ' ', regex=True).str.strip()
    return titles.where(titles.str.split(' ').str.len() >= min_words)


def dedup_step(df, key):
    ''' Merge rows sharing a non-null key: keep the row with highest relevance_score
    and collect the thematic matches of all merged rows '''
    df = df.sort_values('relevance_score', ascending=False, kind='stable')
    has_key = df[key].notna()
    matches = df[has_key].groupby(key, sort=False)['thematic_matches'].agg(
        lambda match_lists: [match for match_list in match_lists for match in match_list])
    df_dedup = df[has_key].drop_duplicates(key, keep='first').copy()
    df_dedup['thematic_matches'] = df_dedup[key].map(matches)
    return pd.concat([df_dedup, df[~has_key]])


def assign_thematic_id(matches, so_by_thematic):
    ''' Keep the strategic objective of the most relevant match and all its thematic_ids '''
    top_thematic = max(matches, key=lambda match: match[1])[0]
    top_so = so_by_thematic[top_thematic]
    return ';'.join(sorted({thematic for thematic, _ in matches if so_by_thematic[thematic] == top_so}))


def deduplicate(studies, thematics, keys=('id', 'doi', 'title_key')):
    ''' Cascade deduplication by id -> doi -> normalized title.
    Returns (deduplicated studies, {key: removed rows}) '''
    so_by_thematic = thematics.set_index('thematic_id')['strategic_objective']
    dedup = studies.copy()
    dedup['thematic_matches'] = [[(thematic_id, relevance)] for thematic_id, relevance
                                 in zip(dedup['thematic_id'], dedup['relevance_score'])]
    dedup['title_key'] = title_key(dedup['display_name'])

    removed = {}
    for key in keys:
        n_rows = len(dedup)
        dedup = dedup_step(dedup, key)
        removed[key] = n_rows - len(dedup)

    dedup['thematic_id'] = dedup['thematic_matches'].apply(assign_thematic_id, so_by_thematic=so_by_thematic)
    return dedup.drop(columns=['thematic_matches', 'title_key']).reset_index(drop=True), removed
