import os

# One BLAS thread: see tests/features/__init__.py.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
