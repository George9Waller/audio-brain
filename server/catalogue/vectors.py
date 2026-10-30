"""Reading many vectors out of Postgres without running out of memory.

pgvector hands each vector over as a list of Python floats: about 40 KB for a 1280-d sound embedding, against 5 KB as
a float32 array. Loading every track's at once that way took gigabytes, more than the cluster gives a pod, so bulk
reads go through `rows_with_arrays`: streamed in chunks, with each vector turned into an array as it arrives. Code
that wants every vector in one matrix uses `rows_and_matrices`, which fills the matrix as the rows arrive instead of
keeping a list of arrays and then copying them all into a matrix, which held the vectors twice.
"""

import numpy as np


def rows_with_arrays(queryset, *positions, chunk_size=2000):
    """The rows of a `values_list` queryset, streamed, with the vectors at `positions` as float32 arrays (None stays
    None)."""
    for row in queryset.iterator(chunk_size=chunk_size):
        row = list(row)
        for i in positions:
            if row[i] is not None:
                row[i] = np.asarray(row[i], dtype=np.float32)
        yield tuple(row)


def rows_and_matrices(queryset, *positions, widths=None, chunk_size=2000):
    """(rows, matrices): the rows of a `values_list` queryset, and the vectors at `positions` as one float32 matrix
    each, row-aligned with the rows (which keep None in their place). A missing vector is a row of zeros. `widths`
    gives a position's width for when none of its vectors are there, {position: width}; otherwise it's 0.

    Each matrix is sized from a count first and filled as the rows stream in, so the vectors are held once."""
    expected = queryset.count()
    widths = widths or {}
    rows, matrices = [], {}
    for row in queryset.iterator(chunk_size=chunk_size):
        row = list(row)
        n = len(rows)
        for i in positions:
            if row[i] is None:
                continue
            if i not in matrices:
                matrices[i] = np.zeros((max(expected, n + 1), len(row[i])), dtype=np.float32)
            elif n == len(matrices[i]):  # rows added since the count
                matrices[i] = np.concatenate([matrices[i], np.zeros((max(n // 10, 100), matrices[i].shape[1]), dtype=np.float32)])
            matrices[i][n] = row[i]
            row[i] = None
        rows.append(tuple(row))
    return rows, [matrices[i][:len(rows)] if i in matrices else np.zeros((len(rows), widths.get(i, 0)), dtype=np.float32) for i in positions]


def objects_with_arrays(queryset, *fields, chunk_size=2000):
    """Model instances, streamed, with the vector `fields` as float32 arrays (None stays None)."""
    for obj in queryset.iterator(chunk_size=chunk_size):
        for field in fields:
            value = getattr(obj, field)
            if value is not None:
                setattr(obj, field, np.asarray(value, dtype=np.float32))
        yield obj
