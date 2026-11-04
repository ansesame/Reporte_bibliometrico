''' OpenAlex API client (OQL queries and entities) and public topics snapshot '''
import gzip
import json
import os
import time
from urllib.parse import quote

import requests

from .config import OA_BASE, TOPICS_MANIFEST, TOPIC_FIELDS, MISSING
from .utils import short_id


class OpenAlexClient:
    ''' Requests to the OpenAlex API adding the cost of each response to cost_usd.
    Failed requests are recorded in failures (they return None) '''

    def __init__(self, api_key, sleep_s=0.5):
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.sleep_s = sleep_s
        self.cost_usd = 0
        self.failures = []

    def validate_oql(self, oql):
        ''' Validate OQL with the free translate endpoint /query/oql (does not run the query).
        Returns (valid, body) '''
        req = requests.get(OA_BASE + "query/oql/" + quote(oql, safe=""), headers=self.headers, timeout=60)
        try:
            body = req.json()
        except ValueError:
            print(f"Error: {req.status_code}")
            return False, None
        validation = body.get("validation", {})
        valid = req.status_code == 200 and validation.get("valid", False)
        if not valid:
            print(f"Invalid OQL ({req.status_code}):", validation.get("errors", body))
        # url_not_representable is expected: OQL has no classic URL equivalent
        warnings = [w for w in validation.get("warnings", []) if w.get("type") != "url_not_representable"]
        if warnings:
            print("Warnings:", warnings)
        return valid, body

    def request_oql(self, oql, per_page=100, max_results=10_000, select=None):
        ''' POST to the API root with the OQL, paginating with cursor. Returns {meta, results} (None if the first page fails) '''
        data = None
        results = []
        cursor = "*"
        pages, total_cost, total_time_ms = 0, 0, 0
        complete = True

        while cursor is not None and len(results) < max_results:
            payload = {"oql": oql, "per_page": per_page, "cursor": cursor}
            if select:
                payload["select"] = ",".join(select)
            req = requests.post(OA_BASE, json=payload, headers=self.headers, timeout=300)
            if req.status_code != 200:
                try:
                    print(f"Error: {req.status_code}", req.json().get("validation"))
                except ValueError:
                    print(f"Error: {req.status_code}")
                self.failures.append({'path': 'oql', 'status': req.status_code, 'cursor': cursor})
                if data is None:
                    return None # First page failed
                complete = False # Keep pages already fetched
                break

            data_page = req.json()
            if data is None:
                data = data_page
                if data['meta']['count'] > max_results:
                    print(f"Warning: Query has {data['meta']['count']} results, only {max_results} will be fetched")

            results.extend(data_page['results'])
            total_cost += data_page['meta']['cost_usd']
            total_time_ms += data_page['meta']['db_response_time_ms']
            cursor = data_page['meta']['next_cursor']
            pages += 1
            if cursor is not None:
                time.sleep(self.sleep_s)

        self.cost_usd += total_cost
        # Cursor paging sorted by relevance can repeat a few works between pages
        n_fetched = len(results)
        if all('id' in result for result in results):
            results = list({result['id']: result for result in results}.values())

        data['results'] = results
        data['meta'].update({'pages': pages, 'cost_usd': total_cost, 'db_response_time_ms': total_time_ms,
                             'next_cursor': cursor, 'complete': complete,
                             'duplicates_removed': n_fetched - len(results)})
        return data

    def get(self, path, params, retries=3):
        ''' GET to the API with retries (None if every attempt fails) '''
        req = None
        for attempt in range(retries):
            try:
                req = requests.get(OA_BASE + path, params=params, headers=self.headers, timeout=60)
                if req.status_code == 200:
                    data = req.json()
                    self.cost_usd += data['meta'].get('cost_usd') or 0
                    return data
            except (requests.RequestException, ValueError):
                pass
            time.sleep(2 * (attempt + 1))
        status = req.status_code if req is not None else 'unknown'
        print(f"Warning: {path} failed ({params}) with code {status}")
        self.failures.append({'path': path, 'status': status, 'params': params})
        return None

    def fetch_entities(self, entity, ids, select):
        ''' Entities by id in batches of 50 ({id: entity}) '''
        ids = sorted({short_id(openalex_id) for openalex_id in ids if openalex_id and openalex_id != MISSING})
        entities = {}
        for start in range(0, len(ids), 50):
            data = self.get(entity, {"filter": "ids.openalex:" + "|".join(ids[start:start + 50]),
                                     "per_page": 50, "select": select})
            entities.update({result['id']: result for result in (data or {}).get('results', [])})
        return entities


def fetch_topics_snapshot(path):
    ''' OpenAlex topics from the public S3 snapshot as {meta, results} (read from the backup if it exists) '''
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    manifest = requests.get(TOPICS_MANIFEST, timeout=60).json()
    records = []
    for file in manifest['files']:
        req = requests.get(file['url'].replace('s3://openalex/', 'https://openalex.s3.amazonaws.com/'), timeout=300)
        req.raise_for_status()
        records += [{k: v for k, v in json.loads(line).items() if k in TOPIC_FIELDS}
                    for line in gzip.decompress(req.content).decode('utf-8').splitlines() if line]
    assert len(records) == manifest['record_count'], "Incomplete topics snapshot"
    snapshot = {'meta': {k: v for k, v in manifest.items() if k != 'files'}, 'results': records}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False)
    return snapshot
