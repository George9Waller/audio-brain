"""Reading many vectors out of Postgres without running out of memory.

pgvector hands each vector over as a list of Python floats: about 40 KB for a 1280-d sound embedding, against 5 KB as
a float32 array. Loading every track's at once that way took gigabytes, more than the cluster gives a pod, so bulk
reads go through `rows_with_arrays`: streamed in chunks, with each vector turned into an array as it arrives.
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


def objects_with_arrays(queryset, *fields, chunk_size=2000):
    """Model instances, streamed, with the vector `fields` as float32 arrays (None stays None)."""
    for obj in queryset.iterator(chunk_size=chunk_size):
        for field in fields:
            value = getattr(obj, field)
            if value is not None:
                setattr(obj, field, np.asarray(value, dtype=np.float32))
        yield obj
