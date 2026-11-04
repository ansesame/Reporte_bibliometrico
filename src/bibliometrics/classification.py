''' Classification of the institutions origin with Claude (structured output) '''
import json
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from .config import (MISSING, INST_COLS, INST_GROUP_COL, CLAUDE_MODEL, CLASSIFY_BATCH_SIZE,
                     TOKENS_PER_INSTITUTION, CLAUDE_PRICE_PER_MTOK)
from .utils import split_list, join_list, group_counts

INST_CONTEXT_FIELDS = {'name': 'display_name', 'country_code': 'country_code', 'type': 'type', 'root': 'root_display_name'}


def inst_groups(country):
    ''' Institution groups for a country report '''
    return (country, "LAC", "USA & Canada", "Europe", "Multilateral organizations", "Other institutions", MISSING)


def system_prompt(country):
    ''' System prompt of the classification '''
    return f"""Clasifica el origen de las instituciones que participan en estudios académicos sobre {country} para un reporte bibliométrico.

Recibes una lista JSON de instituciones. Cada institución tiene un "key", su "name", "country_code" (ISO-2), "type" (tipo de OpenAlex) y "root" (organización padre raíz). Un valor null indica que el dato no está disponible.

Categorías:
- "{country}": instituciones de {country}.
- "LAC": instituciones de América Latina y el Caribe, excluyendo {country}.
- "USA & Canada": instituciones de Estados Unidos (US, USA) y Canadá.
- "Europe": instituciones de países europeos (incluye Reino Unido).
- "Multilateral organizations": BID/IDB, Banco Mundial, OCDE, FMI, Naciones Unidas y sus agencias (PNUD, UNICEF, FAO, OIT...), CEPAL, OPS/OMS, BCIE, SICA y similares, sin importar su country_code (por ejemplo, el Banco Mundial tiene country_code US pero es multilateral).
- "Other institutions": instituciones del resto del mundo.
- "{MISSING}": no es posible determinar el origen con la información disponible.

Reglas:
- Asigna exactamente una categoría a cada institución.
- Organismos multilaterales ("Multilateral organizations") tiene prioridad sobre el país. Usa también "root" para identificarlos (por ejemplo, una oficina nacional cuyo root es UNICEF).
- Si country_code es null, infiere el país a partir del nombre solo cuando sea evidente.
- Devuelve exactamente un resultado por cada key recibido."""


def batch_models(country):
    ''' Pydantic output models with the groups of the country '''
    class InstitutionGroup(BaseModel):
        key: int
        inst_group: Literal[inst_groups(country)]

    class InstGroupBatch(BaseModel):
        institutions: list[InstitutionGroup]

    return InstGroupBatch


def build_inst_table(studies):
    ''' Unique institutions of the studies (index: institution id) with the fields used in the classification '''
    columns = ['authorships.institutions.id'] + [f'authorships.institutions.{var}' for var in INST_CONTEXT_FIELDS.values()]
    rows = {values[0]: values[1:] for _, row in studies[columns].iterrows() for values in zip(*map(split_list, row))}
    return pd.DataFrame.from_dict(rows, orient='index', columns=list(INST_CONTEXT_FIELDS))


def classify_inst_batch(client, batch_df, country, output_model, max_tokens):
    ''' Classify a batch of unique institutions. Returns ({institution id: group}, usage) '''
    contexts = [{"key": key, **{field: None if value == MISSING else value for field, value in row.items()}}
                for key, (_, row) in enumerate(batch_df.iterrows())]
    response = client.messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system_prompt(country),
        messages=[{"role": "user", "content": json.dumps(contexts, ensure_ascii=False)}],
        output_format=output_model,
    )
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        print(f"Warning: batch not classified (stop_reason: {response.stop_reason})")
        return {}, response.usage
    inst_ids = batch_df.index
    groups = {inst_ids[inst.key]: inst.inst_group for inst in response.parsed_output.institutions
              if 0 <= inst.key < len(inst_ids)}
    return groups, response.usage


def classify_institutions(client, studies, country, batch_size=CLASSIFY_BATCH_SIZE):
    ''' Group of each unique institution, aligned with the institutions columns in INST_GROUP_COL
    (MISSING if not classified). Returns (studies, stats) '''
    import anthropic

    inst_table = build_inst_table(studies)
    output_model = batch_models(country)
    groups_by_id, input_tokens, output_tokens, failed_batches = {}, 0, 0, 0
    for start in range(0, len(inst_table), batch_size):
        try:
            groups, usage = classify_inst_batch(client, inst_table.iloc[start:start + batch_size], country,
                                                output_model, TOKENS_PER_INSTITUTION * batch_size)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            print(f"Error in batch {start}: {e}")
            failed_batches += 1
            continue
        groups_by_id.update(groups)
        input_tokens += usage.input_tokens
        output_tokens += usage.output_tokens

    studies = studies.copy()
    studies[INST_GROUP_COL] = studies['authorships.institutions.id'].map(
        lambda inst_ids: join_list([groups_by_id.get(inst_id, MISSING) for inst_id in split_list(inst_ids)]))
    assert (group_counts(studies, INST_COLS + [INST_GROUP_COL]).nunique(axis=1) == 1).all(), "Misaligned inst_group"
    stats = {'unique_institutions': len(inst_table),
             'unclassified_institutions': int((~inst_table.index.isin(list(groups_by_id))).sum()),
             'failed_batches': failed_batches, 'input_tokens': input_tokens, 'output_tokens': output_tokens,
             'cost_usd': input_tokens / 1e6 * CLAUDE_PRICE_PER_MTOK['input'] + output_tokens / 1e6 * CLAUDE_PRICE_PER_MTOK['output']}
    return studies, stats
