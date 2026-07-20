import ctypes
import heapq
import os
import platform
from typing import Dict, List, Optional, Tuple

_lib = None
_lib_loaded = False


def _detect_lib_path() -> Optional[str]:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("aarch64", "arm64"):
        arch = "aarch64"
    else:
        arch = machine

    if system == "windows":
        filename = "vector_engine.dll"
    elif system == "darwin":
        filename = "libvector_engine.dylib"
    else:
        filename = "libvector_engine.so"

    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    dll_dir = os.path.join(base, "dll", f"{system}-{arch}")
    path = os.path.join(dll_dir, filename)
    if os.path.isfile(path):
        return path

    path = os.path.join(base, "dll", filename)
    if os.path.isfile(path):
        return path

    return None


def _load_lib():
    global _lib, _lib_loaded
    if _lib_loaded:
        return _lib
    _lib_loaded = True

    path = _detect_lib_path()
    if not path:
        return None

    try:
        lib = ctypes.CDLL(path)

        lib.vector_dot.argtypes = [
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
        ]
        lib.vector_dot.restype = ctypes.c_float

        lib.vector_batch_dot.argtypes = [
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_float),
        ]
        lib.vector_batch_dot.restype = None

        lib.vector_get_simd_level.argtypes = []
        lib.vector_get_simd_level.restype = ctypes.c_int

        _lib = lib
        return _lib
    except OSError:
        return None


class _NativeImpl:
    def __init__(self, lib):
        self._lib = lib

    def batch_dot(self, query: List[float], vecs: List[List[float]]) -> List[float]:
        dim = len(query)
        n = len(vecs)
        query_arr = (ctypes.c_float * dim)(*query)
        docs_arr = (ctypes.c_float * (n * dim))()
        for i, vec in enumerate(vecs):
            offset = i * dim
            for j in range(dim):
                docs_arr[offset + j] = vec[j]
        out = (ctypes.c_float * n)()
        self._lib.vector_batch_dot(query_arr, docs_arr, ctypes.c_int(n), ctypes.c_int(dim), out)
        return list(out)

    def dot(self, a: List[float], b: List[float]) -> float:
        dim = len(a)
        arr_a = (ctypes.c_float * dim)(*a)
        arr_b = (ctypes.c_float * dim)(*b)
        return self._lib.vector_dot(arr_a, arr_b, ctypes.c_int(dim))


class _PythonImpl:
    def batch_dot(self, query: List[float], vecs: List[List[float]]) -> List[float]:
        from operator import mul
        return [sum(map(mul, query, vec)) for vec in vecs]

    def dot(self, a: List[float], b: List[float]) -> float:
        from operator import mul
        return sum(map(mul, a, b))


class VectorEngine:
    def __init__(self):
        lib = _load_lib()
        if lib is not None:
            self._impl = _NativeImpl(lib)
            self.backend = "native"
        else:
            self._impl = _PythonImpl()
            self.backend = "python"

    def dot(self, a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        return self._impl.dot(a, b)

    def batch_topk(
        self,
        query: List[float],
        vectors: Dict[str, List[float]],
        top_k: int = 10,
        threshold: float = 0.3,
    ) -> List[Tuple[str, float]]:
        ids = []
        vecs = []
        dim = len(query)
        for rid, vec in vectors.items():
            if vec and len(vec) == dim:
                ids.append(rid)
                vecs.append(vec)

        if not vecs:
            return []

        scores = self._impl.batch_dot(query, vecs)

        candidates = []
        for i, score in enumerate(scores):
            if score > threshold:
                candidates.append((score, ids[i]))

        if not candidates:
            return []

        top = heapq.nlargest(min(top_k, len(candidates)), candidates)
        return [(rid, score) for score, rid in top]


_engine: Optional[VectorEngine] = None


def get_engine() -> VectorEngine:
    global _engine
    if _engine is None:
        _engine = VectorEngine()
    return _engine
