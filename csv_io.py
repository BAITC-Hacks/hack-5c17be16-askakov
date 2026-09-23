"""Preserve identifier text at CSV boundaries (CSV itself has no dtype metadata)."""
import csv

import pandas as pd

IDENTIFIER_COLUMNS = ('gid', 'src', 'dst', 'top_gids')


def write_csv(frame, path):
    exported = frame.copy()
    for column in IDENTIFIER_COLUMNS:
        if column in exported:
            exported[column] = exported[column].astype(str)
    exported.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)


def read_csv(path):
    return pd.read_csv(path, dtype={column: str for column in IDENTIFIER_COLUMNS})
