''' OpenAlex API client (OQL queries, entities and full catalogs) and topics catalog '''
import json
import math
import os
import time
from urllib.parse import quote

import requests
from tqdm import tqdm

from .config import OA_BASE, TOPIC_FIELDS, MISSING
from .utils import short_id, progress

RETRY_STATUS = {429, 500, 502, 503, 504} # Transient responses worth retrying


class OpenAlexClient:
    ''' Requests to the OpenAlex API adding the cost of each response to cost_usd.
    Failed requests are recorded in failures (they return None) '''

    def __init__(self, api_key, sleep_s=0.5, retries=5, backoff_s=5):
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.sleep_s = sleep_s
        self.retries = retries
        self.backoff_s = backoff_s
        self.cost_usd = 0
        self.failures = []

    def _send(self, method, url, **kwargs):
        ''' HTTP request retrying network errors (DNS, connection, timeout) and transient statuses
        with exponential backoff. Returns the last response, or None if every attempt hit a network error '''
        req = None
        for attempt in range(self.retries):
            try:
                req = requests.request(method, url, headers=self.headers, **kwargs)
                if req.status_code not in RETRY_STATUS:
                    return req
                reason = req.status_code
            except (requests.ConnectionError, requests.Timeout) as e:
                req, reason = None, type(e).__name__
            if attempt < self.retries - 1:
                wait = self.backoff_s * 2 ** attempt
                tqdm.write(f"Retry {attempt + 1}/{self.retries - 1} in {wait}s ({reason})")
                time.sleep(wait)
        return req

    def validate_oql(self, oql):
        ''' Validate OQL with the free translate endpoint /query/oql (does not run the query).
        Returns (valid, body) '''
        req = self._send("GET", OA_BASE + "query/oql/" + quote(oql, safe=""), timeout=60)
        if req is None:
            print("Error: network failure validating the OQL")
            return False, None
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
            req = self._send("POST", OA_BASE, json=payload, timeout=300)
            if req is None or req.status_code != 200:
                status = req.status_code if req is not None else 'network_error'
                try:
                    print(f"Error: {status}", req.json().get("validation"))
                except (AttributeError, ValueError):
                    print(f"Error: {status}")
                self.failures.append({'path': 'oql', 'status': status, 'cursor': cursor})
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
        tqdm.write(f"Warning: {path} failed ({params}) with code {status}")
        self.failures.append({'path': path, 'status': status, 'params': params})
        return None

    def fetch_all(self, path, select, desc, per_page=200):
        ''' Every record of an entity list (topics, subfields...) paging with cursor.
        Returns None if a page fails or the number of records differs from meta.count '''
        params = {"per_page": per_page, "select": select, "cursor": "*"}
        data = self.get(path, params)
        if data is None:
            return None
        records = list(data['results'])
        with tqdm(total=math.ceil(data['meta']['count'] / per_page), desc=desc, dynamic_ncols=True) as bar:
            bar.update(1)
            while data['meta'].get('next_cursor') and data['results']:
                data = self.get(path, {**params, "cursor": data['meta']['next_cursor']})
                if data is None:
                    return None
                records += data['results']
                bar.update(1)
        records = list({record['id']: record for record in records}.values())
        return records if len(records) == data['meta']['count'] else None

    def fetch_entities(self, entity, ids, select, desc=None):
        ''' Entities by id in batches of 50 ({id: entity}); desc shows a progress bar '''
        ids = sorted({short_id(openalex_id) for openalex_id in ids if openalex_id and openalex_id != MISSING})
        entities = {}
        starts = range(0, len(ids), 50)
        for start in (progress(starts, desc) if desc else starts):
            data = self.get(entity, {"filter": "ids.openalex:" + "|".join(ids[start:start + 50]),
                                     "per_page": 50, "select": select})
            entities.update({result['id']: result for result in (data or {}).get('results', [])})
        return entities


TOPICS_SOURCE = "openalex_api" # meta.source of the topics catalogs downloaded from the API


def fetch_topics(client, path, refresh):
    ''' OpenAlex topics catalog as {meta, results}, saved in path.
    The backup is reused only if not refresh and it was downloaded from the API (older backups came from the
    S3 snapshot, which can lag behind the topic assignments of the works). Returns (catalog, downloaded) '''
    if not refresh and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            catalog = json.load(f)
        if catalog['meta'].get('source') == TOPICS_SOURCE:
            return catalog, False
    records = client.fetch_all('topics', ",".join(TOPIC_FIELDS), "Topics catalog")
    if records is None:
        raise RuntimeError("The OpenAlex topics catalog could not be downloaded completely: rerun later")
    catalog = {'meta': {'date': time.strftime("%Y-%m-%d"), 'source': TOPICS_SOURCE, 'count': len(records)},
               'results': records}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False)
    return catalog, True
