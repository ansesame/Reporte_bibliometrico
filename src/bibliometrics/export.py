''' Excel and JSON outputs '''
import json

import pandas as pd


def write_excel(path, sheets):
    ''' One sheet per table ({sheet name: dataframe}) '''
    with pd.ExcelWriter(path) as writer:
        for name, table in sheets.items():
            table.to_excel(writer, sheet_name=name, index=False)


def write_json(path, data):
    ''' JSON file in UTF-8 '''
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False, default=str)
