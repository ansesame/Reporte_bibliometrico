''' Bibliometric pipeline: from a queries file (thematic axes with keywords) to the studies file and
the relational database in outputs/.

Usage:
    python scripts/run_pipeline.py data/queries_honduras_ET.json --name bibliometrics_honduras
        [--subfields data/subfields_tagged.xlsx]

Outputs:
    outputs/{name}.xlsx          studies with imputations and thematic fields
    outputs/{name}_db.xlsx       one sheet per table (studies, institutions, topics, thematics, bridges,
                                 citations per year and metadata of the queries file)
    outputs/{name}_backup/       OpenAlex responses per thematic, topics snapshot and run_summary.json

Requires OPENALEX_API_KEY and ANTHROPIC_API_KEY in .env (S2_API_KEY is optional).
The backups are reused when they exist, so a rerun does not pay the queries again.
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
from bibliometrics.config import DROP_COLS
from bibliometrics.database import assign_retrieved_date, build_audit_table, build_db_tables
from bibliometrics.enrichment import blank_to_nan, impute_abstracts, impute_language, impute_publisher, impute_source_oa
from bibliometrics.export import write_excel, write_json
from bibliometrics.openalex import OpenAlexClient, fetch_topics_snapshot
from bibliometrics.queries import load_queries, build_thematics_df, thematic_oql
from bibliometrics.selection import filter_interest_subfields, deduplicate
from bibliometrics.utils import short_id
from bibliometrics.works import build_studies_df


def parse_args():
    parser = argparse.ArgumentParser(description="Bibliometric pipeline over OpenAlex")
    parser.add_argument("queries", help="JSON file with meta and thematics (format of data/queries_honduras_ET.json)")
    parser.add_argument("--name", help="Base name of the outputs (default: bibliometrics_{country})")
    parser.add_argument("--subfields", default=str(ROOT / "data" / "subfields_tagged.xlsx"),
                        help="Excel with the OpenAlex subfield 'id' and 'interest_subfield' (1 = keep)")
    return parser.parse_args()


def log(summary, stage, **values):
    ''' Print and keep the counts of a stage '''
    summary['stages'][stage] = values
    print(f"[{stage}]", ", ".join(f"{k}: {v}" for k, v in values.items()))


def fetch_thematic(client, thematic, meta, backup_dir):
    ''' OpenAlex results of a thematic (from the backup if it exists) '''
    path = backup_dir / f"thematic_{thematic['thematic_id']}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f), True
    data = client.request_oql(thematic_oql(thematic, meta))
    if data is not None:
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
    summary = {'queries_file': str(args.queries), 'subfields_file': str(args.subfields), 'name': name,
               'start': time.strftime("%Y-%m-%d %H:%M:%S"), 'stages': {}, 'costs_usd': {}}
    start_time = time.perf_counter()
    client = OpenAlexClient(os.getenv("OPENALEX_API_KEY"))
    thematics = build_thematics_df(queries)

    # 1. Validate the OQL (free endpoint) and query OpenAlex per thematic
    invalid = [thematic['thematic_id'] for thematic in queries['thematics']
               if not (backup_dir / f"thematic_{thematic['thematic_id']}.json").exists()
               and not client.validate_oql(thematic_oql(thematic, meta))[0]]
    if invalid:
        sys.exit(f"Invalid OQL for thematics {invalid}: fix the keywords before running")

    studies_list, from_backup, failed, counts_by_year = [], 0, [], {}
    for thematic in queries['thematics']:
        data, cached = fetch_thematic(client, thematic, meta, backup_dir)
        if data is None:
            failed.append(thematic['thematic_id'])
            continue
        from_backup += cached
        if not data['meta'].get('complete', True):
            print(f"Warning: thematic {thematic['thematic_id']} is incomplete")
        studies_list.append(build_studies_df(data['results'], thematic['thematic_id']))
        counts_by_year.update({short_id(result['id']): result.get('counts_by_year') or []
                               for result in data['results']})
        print(f"Thematic {thematic['thematic_id']} ({thematic['thematic_axis']}): {len(data['results'])} works")
    if failed:
        sys.exit(f"Thematics {failed} failed: rerun to retry them (the others are kept in {backup_dir})")
    studies = pd.concat(studies_list, ignore_index=True)
    summary['costs_usd']['openalex_queries'] = client.cost_usd
    log(summary, 'queries', thematics=len(studies_list), from_backup=from_backup, studies=len(studies))

    # 2. Subfields of interest and duplicates
    studies = filter_interest_subfields(studies, pd.read_excel(args.subfields))
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
    studies, auth = au.prepare_authorships(client, studies)
    au.add_authors_as_institutions(client, auth)
    au.add_publisher_as_authors(client, auth, studies)
    au.impute_author_ids(client, auth, studies)
    au.add_author_affiliations(client, auth, studies)
    au.standardize_institutions(client, auth)
    studies = au.write_authorship_columns(studies, auth)
    summary['costs_usd']['openalex_imputation'] = client.cost_usd - cost_before
    log(summary, 'authorships', **auth.stats,
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
    topics_snapshot = fetch_topics_snapshot(backup_dir / "topics_snapshot.json")
    tables, topic_diffs = build_db_tables(studies, thematics, topics_snapshot, meta, run_date, counts_by_year)
    log(summary, 'database', **{table: list(df.shape) for table, df in tables.items()},
        topics_snapshot=topics_snapshot['meta']['date'],
        studies_with_topic_differences=int(topic_diffs.any(axis=1).sum()))

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
