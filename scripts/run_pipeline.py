''' Bibliometric pipeline: from a queries file (thematic axes with keywords) to the studies file and
the relational database in outputs/.

Usage:
    python scripts/run_pipeline.py data/queries_{country}.json --subfields data/subfields_tagged_{country}.xlsx
        [--name bibliometrics_{country}] [--no-stemming]

Outputs:
    outputs/{name}.xlsx          studies with imputations and thematic fields
    outputs/{name}_db.xlsx       one sheet per table (studies, institutions, topics, thematics, bridges,
                                 citations per year and metadata of the queries file)
    outputs/{name}_backup/       OpenAlex responses per thematic, topics catalog and run_summary.json

Requires OPENALEX_API_KEY and ANTHROPIC_API_KEY in .env (S2_API_KEY is optional).
The complete backups are reused when they exist, so a rerun does not pay those queries again; failed or
incomplete thematics stop the run before the later stages and are queried again on the next run.
A backup made with another OQL (other types, keywords or stemming) stops the run until it is deleted.
The subfields file is checked against the current OpenAlex subfields, and the topics catalog is downloaded
from the API whenever a thematic is queried, so that it matches the topic assignments of the works.
'''
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
from dotenv import load_dotenv

from bibliometrics import authorships as au
from bibliometrics.classification import classify_institutions
from bibliometrics.config import ALLOWED_TYPES, DROP_COLS, SEARCH_FIELD, SUBFIELD_FIELDS, OA_RATE_SHARE
from bibliometrics.database import assign_retrieved_date, build_audit_table, build_db_tables
from bibliometrics.enrichment import blank_to_nan, impute_abstracts, impute_language, impute_publisher, impute_source_oa
from bibliometrics.export import write_excel, write_json
from bibliometrics.openalex import OpenAlexClient, fetch_topics
from bibliometrics.queries import load_queries, build_thematics_df, thematic_oql
from bibliometrics.selection import check_subfields, filter_interest_subfields, deduplicate
from bibliometrics.utils import short_id
from bibliometrics.works import build_studies_df


def parse_args():
    parser = argparse.ArgumentParser(description="Bibliometric pipeline over OpenAlex")
    parser.add_argument("queries", help="JSON file with meta and thematics (format of data/queries_honduras_ET.json)")
    parser.add_argument("--name", help="Base name of the outputs (default: bibliometrics_{country})")
    parser.add_argument("--subfields", required=True,
                        help="Excel of the country with the OpenAlex subfield 'id' and 'interest_subfield' (1 = keep), "
                             "e.g. data/subfields_tagged_honduras.xlsx")
    parser.add_argument("--stemming", action=argparse.BooleanOptionalAction, default=True,
                        help="Search the keywords with stemming (default); --no-stemming searches them as exact phrases")
    return parser.parse_args()


STAGE_CLOCK = {'last': time.perf_counter()} # End of the previous stage


def log(summary, stage, **values):
    ''' Print and keep the counts of a stage, with time_s: seconds since the previous stage ended '''
    now = time.perf_counter()
    values['time_s'] = round(now - STAGE_CLOCK['last'], 1)
    STAGE_CLOCK['last'] = now
    summary['stages'][stage] = values
    print(f"[{stage}]", ", ".join(f"{k}: {v}" for k, v in values.items()))


def timed(times, step, func, *args):
    ''' func(*args) keeping its duration in times[step] (seconds) '''
    start = time.perf_counter()
    result = func(*args)
    times[step] = round(time.perf_counter() - start, 1)
    return result


def announce_estimate(client, estimate, spent):
    ''' Print the estimated cost of the OpenAlex imputation and the balance of the key (it does not stop the run) '''
    print(f"[estimate] OpenAlex imputation: ~USD {estimate['cost_usd']:.2f} (upper bound USD {estimate['cost_usd_max']:.2f})"
          f" | searches: {estimate['org_searches']} authors as institutions, {estimate['publisher_searches']} publishers,"
          f" up to {estimate['author_searches']} authors | up to {estimate['entity_batches']} entity batches"
          f" | ~{estimate['time_s'] / 60:.0f} min")
    limits = client.rate_limit() # Current balance (free)
    if limits is None:
        print(f"[estimate] Already spent on author entities: USD {spent:.2f} | OpenAlex balance not available")
        return
    daily, prepaid = limits.get('daily_remaining_usd') or 0, limits.get('prepaid_remaining_usd') or 0
    print(f"[estimate] Already spent on author entities: USD {spent:.2f}"
          f" | OpenAlex balance: daily USD {daily:.2f} + prepaid USD {prepaid:.2f}")
    if estimate['cost_usd_max'] > daily + prepaid:
        print("Warning: the estimate exceeds the OpenAlex balance; requests will fail when it runs out and the"
              " imputation will be incomplete (see openalex_failures in run_summary.json)")
    elif estimate['cost_usd_max'] > daily:
        print("Warning: the estimate exceeds the daily balance; the prepaid balance will be used")


def backup_path(backup_dir, thematic_id):
    return backup_dir / f"thematic_{thematic_id}.json"


def backup_oql(path):
    ''' OQL with which the pipeline made a thematic backup (None for backups made before it was recorded) '''
    with open(path, encoding="utf-8") as f:
        return json.load(f)['meta'].get('pipeline_oql')


def fetch_thematic(client, thematic_id, oql, backup_dir):
    ''' OpenAlex results of a thematic (from the backup if it exists and is complete).
    An incomplete backup is queried again; it is only replaced if the new query returns data.
    The OQL is saved in the backup meta as pipeline_oql '''
    path = backup_path(backup_dir, thematic_id)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data['meta'].get('complete', True):
            return data, True
        print(f"Thematic {thematic_id}: backup is incomplete, querying again")
    data = client.request_oql(oql)
    if data is not None:
        data['meta']['pipeline_oql'] = oql
        write_json(path, data)
    return data, False


def main():
    args = parse_args()
    load_dotenv(ROOT / ".env")
    queries = load_queries(args.queries)
    meta = queries['meta']
    name = args.name or f"bibliometrics_{meta['country'].lower().replace(' ', '_')}"
    out_dir = ROOT / "outputs"
    backup_dir = out_dir / f"{name}_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    run_date = time.strftime("%Y-%m-%d")
    search = {'search_fields': SEARCH_FIELD, 'allowed_types': ALLOWED_TYPES, 'stemming': args.stemming}
    summary = {'queries_file': str(args.queries), 'subfields_file': str(args.subfields), 'name': name,
               'start': time.strftime("%Y-%m-%d %H:%M:%S"), 'search': search, 'stages': {}, 'costs_usd': {}}
    start_time = time.perf_counter()
    STAGE_CLOCK['last'] = start_time
    client = OpenAlexClient(os.getenv("OPENALEX_API_KEY"))
    print(f"OpenAlex: {client.rate:.1f} requests/s ({OA_RATE_SHARE:.0%} of the limit of {client.per_second_limit}/s"
          f"{'' if client.limits else ', /rate-limit not available'}), {client.workers} threads")
    thematics = build_thematics_df(queries, args.stemming)
    oqls = {thematic['thematic_id']: thematic_oql(thematic, meta, args.stemming) for thematic in queries['thematics']}

    # 0. Subfields file against the current OpenAlex subfields
    subfields = pd.read_excel(args.subfields)
    live_subfields = client.fetch_all('subfields', ",".join(SUBFIELD_FIELDS), "OpenAlex subfields")
    if live_subfields is None:
        sys.exit("Could not download the OpenAlex subfields to check the subfields file: rerun later")
    check = check_subfields(subfields, live_subfields)
    now = time.perf_counter()
    summary['stages']['subfields_check'] = {**check, 'time_s': round(now - STAGE_CLOCK['last'], 1)}
    STAGE_CLOCK['last'] = now
    print(f"[subfields_check] missing_in_file: {len(check['missing_in_file'])}, "
          f"extra_in_file: {len(check['extra_in_file'])}, changed: {len(check['changed'])}")
    if check['missing_in_file'] or check['extra_in_file']:
        sys.exit(f"The subfields of {args.subfields} differ from OpenAlex: missing in the file "
                 f"{check['missing_in_file']}, not in OpenAlex {check['extra_in_file']}. "
                 "Update the file and tag the new subfields (interest_subfield) before running")
    for change in check['changed']:
        print(f"Warning: subfield {change['id']} {change['column']} is '{change['openalex']}' in OpenAlex "
              f"and '{change['file']}' in the file")
    cost_before = client.cost_usd

    # 1. Check the backups, validate the OQL (free endpoint) and query OpenAlex per thematic
    stale = [path.name for thematic_id, oql in oqls.items()
             if (path := backup_path(backup_dir, thematic_id)).exists() and backup_oql(path) != oql]
    if stale:
        sys.exit(f"The backups {stale} in {backup_dir} were made with another OQL (types, search field, keywords"
                 " or stemming): delete them to query those thematics again")

    invalid, unvalidated = [], []
    for thematic_id, oql in oqls.items():
        if backup_path(backup_dir, thematic_id).exists():
            continue
        valid, body = client.validate_oql(oql)
        if body is None or 'validation' not in body: # Network or API error, not a verdict on the OQL
            unvalidated.append(thematic['thematic_id'])
        elif not valid:
            invalid.append(thematic['thematic_id'])
    if invalid:
        sys.exit(f"Invalid OQL for thematics {invalid}: fix the keywords before running")
    if unvalidated:
        sys.exit(f"Could not validate the OQL of thematics {unvalidated} (network or API error): rerun later")

    studies_list, from_backup, failed, counts_by_year = [], 0, [], {}
    for thematic in queries['thematics']:
        data, cached = fetch_thematic(client, thematic['thematic_id'], oqls[thematic['thematic_id']], backup_dir)
        if data is None or not data['meta'].get('complete', True):
            failed.append(thematic['thematic_id'])
            continue
        from_backup += cached
        studies_list.append(build_studies_df(data['results'], thematic['thematic_id']))
        counts_by_year.update({short_id(result['id']): result.get('counts_by_year') or []
                               for result in data['results']})
        print(f"Thematic {thematic['thematic_id']} ({thematic['thematic_axis']}): {len(data['results'])} works")
    if failed:
        sys.exit(f"Thematics {failed} failed or are incomplete: rerun to retry them "
                 f"(the complete ones are kept in {backup_dir})")
    studies = pd.concat(studies_list, ignore_index=True)
    summary['costs_usd']['openalex_queries'] = client.cost_usd - cost_before
    log(summary, 'queries', thematics=len(studies_list), from_backup=from_backup, studies=len(studies))

    # Topics catalog of the same date as the works: downloaded again if a thematic was queried in this run
    # (fetched here so that a failure stops the run before the paid imputations)
    try:
        topics_snapshot, topics_downloaded = fetch_topics(client, backup_dir / "topics_snapshot.json",
                                                          refresh=from_backup < len(studies_list))
    except RuntimeError as e:
        sys.exit(str(e))

    # 2. Subfields of interest and duplicates
    studies = filter_interest_subfields(studies, subfields)
    log(summary, 'subfields', studies=len(studies))
    studies, removed = deduplicate(studies, thematics)
    log(summary, 'duplicates', **{f'removed_by_{key}': n for key, n in removed.items()}, studies=len(studies),
        multiple_thematics=int(studies['thematic_id'].str.contains(';').sum()))

    # 3. Abstracts, language, source open access and publisher
    studies = blank_to_nan(studies)
    studies = impute_abstracts(studies, os.getenv("S2_API_KEY"))
    log(summary, 'abstracts', **studies['abstract_source'].fillna('none').value_counts().to_dict())
    studies = impute_language(studies)
    log(summary, 'language', imputed=int(studies['language_imputed'].sum()),
        without_language=int(studies['language'].isna().sum()))
    studies = impute_source_oa(studies)
    log(summary, 'source_oa', imputed=int(studies['source_is_oa_imputed'].sum()),
        without_value=int(studies['primary_location.source.is_oa'].isna().sum()))
    studies = impute_publisher(studies)
    log(summary, 'publisher', **studies['publisher_source'].fillna('none').value_counts().to_dict())
    if studies['publisher'].isna().any():
        print("Warning: studies without publisher:", studies['publisher'].isna().sum())

    # 4. Authors and institutions
    cost_before = client.cost_usd
    steps_time = {}
    country_terms = [meta['country']] + meta['country_terms']
    studies, auth = timed(steps_time, 'prepare_authorships', au.prepare_authorships, client, studies)
    estimate = au.estimate_imputation_cost(client, auth, studies, country_terms)
    announce_estimate(client, estimate, spent=client.cost_usd - cost_before)
    summary['imputation_estimate'] = estimate
    summary['costs_usd']['openalex_imputation_estimate'] = round(client.cost_usd - cost_before + estimate['cost_usd'], 4)
    timed(steps_time, 'authors_as_institutions', au.add_authors_as_institutions, client, auth, country_terms)
    timed(steps_time, 'publisher_as_authors', au.add_publisher_as_authors, client, auth, studies)
    timed(steps_time, 'impute_author_ids', au.impute_author_ids, client, auth, studies)
    timed(steps_time, 'author_affiliations', au.add_author_affiliations, client, auth, studies)
    timed(steps_time, 'standardize_institutions', au.standardize_institutions, client, auth)
    studies = au.write_authorship_columns(studies, auth)
    summary['costs_usd']['openalex_imputation'] = client.cost_usd - cost_before
    log(summary, 'authorships', **auth.stats, steps_time_s=steps_time,
        studies_without_authors_after=int(studies['authorships.author.display_name'].isna().sum()),
        studies_without_institutions_after=int(studies['authorships.institutions.id'].isna().sum()))
    studies = studies.drop(columns=DROP_COLS)

    # 5. Institution groups with Claude
    import anthropic
    studies, class_stats = classify_institutions(anthropic.Anthropic(), studies, meta['country'])
    summary['costs_usd']['anthropic'] = class_stats.pop('cost_usd')
    log(summary, 'inst_groups', **class_stats)

    # 6. Outputs
    studies_path, db_path = out_dir / f"{name}.xlsx", out_dir / f"{name}_db.xlsx"
    studies, kept = assign_retrieved_date(studies, db_path, run_date) # Reads the previous database before overwriting it
    log(summary, 'retrieved_date', kept_previous=kept, new=len(studies) - kept, run_date=run_date)
    tables, topic_diffs = build_db_tables(studies, thematics, topics_snapshot, {**meta, **search}, run_date,
                                          counts_by_year)
    log(summary, 'database', **{table: list(df.shape) for table, df in tables.items()},
        topics_snapshot=topics_snapshot['meta']['date'], topics_source=topics_snapshot['meta']['source'],
        topics_downloaded=topics_downloaded,
        studies_with_topic_differences=int(topic_diffs.any(axis=1).sum()))
    if topic_diffs.any(axis=1).any():
        print(f"Warning: {int(topic_diffs.any(axis=1).sum())} studies have a topic hierarchy different from the"
              " topics catalog (the catalog is older than some works). See README, 'Reutilización de respaldos'")

    summary['openalex_requests'] = client.settings()
    summary['openalex_failures'] = client.failures
    summary['costs_usd']['openalex_total'] = client.cost_usd
    summary['time_s'] = round(time.perf_counter() - start_time, 1)
    if client.failures:
        print(f"Warning: {len(client.failures)} OpenAlex requests failed (budget or connection);"
              " the imputations may be incomplete. See run_summary.json")

    write_excel(studies_path, {'studies': build_audit_table(studies, thematics)})
    write_excel(db_path, tables)
    write_json(backup_dir / "run_summary.json", summary)
    print("Costs USD:", {k: round(v, 4) for k, v in summary['costs_usd'].items()})
    print("Outputs:", studies_path, db_path, sep="\n  ")


if __name__ == "__main__":
    main()
