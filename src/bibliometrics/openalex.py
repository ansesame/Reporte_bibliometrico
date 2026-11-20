''' OpenAlex API client (OQL queries, entities and full catalogs) and topics catalog '''
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests
from tqdm import tqdm

from .config import OA_BASE, TOPIC_FIELDS, MISSING, OA_RATE_SHARE, OA_WORKERS, OA_FALLBACK_RPS
from .utils import short_id, progress

RETRY_STATUS = {429, 500, 502, 503, 504} # Transient responses worth retrying
MAX_THROTTLED_RETRIES = 10 # Retries of a request refused by a per-second limit (429), not counted as attempts


class RateLimiter:
    ''' At most `rate` request starts per second, shared by all the threads: each call reserves the next slot,
    spaced 1/rate seconds after the previous one, and waits until it '''

    def __init__(self, rate):
        self.interval = 1 / rate
        self.next_slot = time.monotonic()
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            slot = max(time.monotonic(), self.next_slot)
            self.next_slot = slot + self.interval
        time.sleep(max(0, slot - time.monotonic()))


def retry_after(req):
    ''' Seconds to wait after a 429 (Retry-After header, 1 by default) '''
    try:
        return max(float(req.headers.get('Retry-After', 1)), 0.5)
    except ValueError:
        return 1


def limit_reason(req):
    ''' reason of a 429 response (requests_per_second, daily_budget...) or None '''
    try:
        return req.json().get('reason')
    except ValueError:
        return None


class OpenAlexClient:
    ''' Requests to the OpenAlex API adding the cost of each response to cost_usd.
    Every request goes through a rate limiter shared by the threads of pmap: OA_RATE_SHARE of the per-second
    limit of the key, read from the free /rate-limit endpoint. Failed requests are recorded in failures
    (they return None) '''

    def __init__(self, api_key, sleep_s=0.5, retries=5, backoff_s=5, workers=OA_WORKERS):
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.sleep_s = sleep_s
        self.retries = retries
        self.backoff_s = backoff_s
        self.workers = workers
        self.cost_usd = 0
        self.failures = []
        self.throttled = 0 # 429 responses for a per-second limit (retried)
        self.lock = threading.Lock()
        self._local = threading.local()
        self.limits = self.rate_limit() if api_key else None
        self.per_second_limit = ((self.limits or {}).get('per_second_limits') or {}).get('all_requests') or OA_FALLBACK_RPS
        self.rate = OA_RATE_SHARE * self.per_second_limit
        self.limiter = RateLimiter(self.rate)

    @property
    def session(self):
        ''' requests.Session of the current thread (connections are reused, sessions are not shared) '''
        if not hasattr(self._local, 'session'):
            self._local.session = requests.Session()
        return self._local.session

    def rate_limit(self):
        ''' Limits, costs and balance of the key from /rate-limit (free). None if it cannot be read '''
        try:
            req = self.session.get(OA_BASE + "rate-limit", headers=self.headers, timeout=30)
            return req.json().get('rate_limit') if req.status_code == 200 else None
        except (requests.RequestException, ValueError):
            return None

    def settings(self):
        ''' Request settings for run_summary.json '''
        return {'per_second_limit': self.per_second_limit, 'rate_per_second': round(self.rate, 2),
                'workers': self.workers, 'throttled_429': self.throttled}

    def _add_cost(self, cost):
        with self.lock:
            self.cost_usd += cost or 0

    def _fail(self, record):
        with self.lock:
            self.failures.append(record)

    def pmap(self, func, items, desc=None):
        ''' [func(item) for item in items] in OA_WORKERS threads, in the order of items; desc shows a progress bar.
        The requests of every thread go through the shared rate limiter '''
        items = list(items)
        if not items:
            return []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            results = pool.map(func, items)
            return list(progress(results, desc, total=len(items)) if desc else results)

    def _send(self, method, url, **kwargs):
        ''' HTTP request retrying network errors (DNS, connection, timeout) and transient statuses
        with exponential backoff. Returns the last response, or None if every attempt hit a network error '''
        req = None
        for attempt in range(self.retries):
            self.limiter.wait()
            try:
                req = self.session.request(method, url, headers=self.headers, **kwargs)
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
                self._fail({'path': 'oql', 'status': status, 'cursor': cursor})
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

        self._add_cost(total_cost)
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
        ''' GET to the API through the rate limiter (None if every attempt fails).
        A 429 for a per-second limit waits Retry-After and is retried without spending an attempt; a 429 for
        the daily budget and other 4xx responses (e.g. 400 for invalid characters) are not retried '''
        req, attempt, throttled = None, 0, 0
        while attempt < retries:
            self.limiter.wait()
            try:
                req = self.session.get(OA_BASE + path, params=params, headers=self.headers, timeout=60)
            except requests.RequestException:
                req = None
            if req is not None and req.status_code == 200:
                try:
                    data = req.json()
                    self._add_cost(data['meta'].get('cost_usd'))
                    return data
                except (ValueError, KeyError):
                    pass
            elif req is not None and req.status_code == 429 and limit_reason(req) != 'daily_budget':
                if throttled < MAX_THROTTLED_RETRIES:
                    throttled += 1
                    with self.lock:
                        self.throttled += 1
                    time.sleep(retry_after(req))
                    continue
            elif req is not None and 400 <= req.status_code < 500:
                break
            attempt += 1
            if attempt < retries:
                time.sleep(2 * attempt)
        status = req.status_code if req is not None else 'unknown'
        reason = limit_reason(req) if req is not None and req.status_code == 429 else None
        tqdm.write(f"Warning: {path} failed ({params}) with code {status}" + (f" ({reason})" if reason else ""))
        self._fail({'path': path, 'status': status, 'params': params, **({'reason': reason} if reason else {})})
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
        ''' Entities by id in batches of 50, requested in parallel ({id: entity}); desc shows a progress bar '''
        ids = sorted({short_id(openalex_id) for openalex_id in ids if openalex_id and openalex_id != MISSING})
        batches = [ids[start:start + 50] for start in range(0, len(ids), 50)]
        pages = self.pmap(lambda batch: self.get(entity, {"filter": "ids.openalex:" + "|".join(batch),
                                                          "per_page": 50, "select": select}), batches, desc)
        return {result['id']: result for data in pages for result in (data or {}).get('results', [])}


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
