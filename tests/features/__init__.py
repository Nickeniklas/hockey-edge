import os

# numpy's and scipy's OpenBLAS each commit a buffer per thread when they are
# imported: about 1 GB together on a 16-thread machine, almost all of the
# suite's committed memory and the likely cause of the MemoryError seen
# under load. The tests need one thread. Set before anything imports numpy;
# the same line is in tests/models and tests/validation, which import it
# directly.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
