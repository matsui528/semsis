from dataclasses import dataclass
from os import PathLike
from typing import Any, Dict, Optional, Tuple

import faiss
import numpy as np

from semsis.retriever.base import Retriever

MetricType = int


def faiss_index_builder(
    cfg: "RetrieverFaiss.Config", dim: int, metric: MetricType
) -> faiss.Index:
    """Build a faiss index from the given configuration.

    Args:
        cfg (RetrieverFaiss.Config):
        dim (int):
        metric: MetricType

    Returns:
        faiss.Index: Faiss index.
    """
    if cfg.ivf:
        if cfg.hnsw:
            cq = faiss.IndexHNSWFlat(dim, cfg.hnsw_edges, metric)
        else:
            cq = faiss.IndexFlat(dim, metric)

        if cfg.pq:
            return faiss.IndexIVFPQ(
                cq, dim, cfg.ivf_lists, cfg.pq_subvec, cfg.pq_nbits, metric
            )
        else:
            return faiss.IndexIVFFlat(cq, dim, cfg.ivf_lists, metric)
    else:
        if cfg.pq:
            if cfg.hnsw:
                return faiss.IndexHNSWPQ(dim, cfg.pq_subvec, cfg.hnsw_edges)
            return faiss.IndexPQ(dim, cfg.pq_subvec, cfg.pq_nbits, metric)
        else:
            if cfg.hnsw:
                return faiss.IndexHNSWFlat(dim, cfg.hnsw_edges, metric)
            return faiss.IndexFlat(dim, metric)


class RetrieverFaiss(Retriever):
    """Faiss retriever classes.

    Args:
        index (faiss.Index): Index object.
        cfg (FaissRetriever.Config): Configuration dataclass.
    """

    index: faiss.Index
    cfg: "Config"

    METRICS_MAP: Dict[str, MetricType] = {
        "l2": faiss.METRIC_L2,
        "ip": faiss.METRIC_INNER_PRODUCT,
        "cos": faiss.METRIC_INNER_PRODUCT,
    }

    @dataclass
    class Config(Retriever.Config):
        """Configuration of the retriever."""

        hnsw_edges: int = 0
        ivf_lists: int = 0
        pq_subvec: int = 0
        pq_nbits: int = 8
        opq: bool = False
        pca: bool = False
        pca_dim: int = 0

        def __post_init__(self):
            self.hnsw = self.hnsw_edges > 0
            self.ivf = self.ivf_lists > 0
            self.pq = self.pq_subvec > 0
            if self.opq and self.pca:
                raise ValueError("`opq` and `pca` cannot be set True at the same time.")
            self.transform = self.opq or self.pca

            if self.pca and self.pca_dim <= 0:
                self.pca_dim = self.dim

    def __len__(self) -> int:
        """Return the size of the index."""
        return self.index.ntotal

    @classmethod
    def build(cls, cfg: "Config") -> "RetrieverFaiss":
        """Build this class from the given configuration.

        Args:
            cfg (Retriever.Config): Configuration.

        Returns:
            RetrieverFaiss: This class with the constucted index object.
        """
        metric = cls.METRICS_MAP[cfg.metric]
        dim = cfg.pca_dim if cfg.pca else cfg.dim

        index = faiss_index_builder(cfg, dim, metric)
        if not cfg.ivf:
            index = faiss.IndexIDMap(index)

        if cfg.opq:
            vtrans = faiss.OPQMatrix(cfg.dim, M=cfg.pq_subvec)
            index = faiss.IndexPreTransform(vtrans, index)
        elif cfg.pca:
            vtrans = faiss.PCAMatrix(cfg.dim, d_out=cfg.pca_dim)
            index = faiss.IndexPreTransform(vtrans, index)
        return cls(index, cfg)

    def set_nprobe(self, nprobe: int) -> None:
        """Set nprobe parameter for IVF-family indexes.

        Args:
            nprobe (int): Number of nearest neighbor clusters that are
              probed in search time.
        """
        if self.cfg.ivf:
            faiss.extract_index_ivf(self.index).nprobe = nprobe

    def set_efsearch(self, efsearch: int) -> None:
        """Set efSearch parameter for HNSW indexes.

        Args:
            efsearch (int): The depth of exploration of the search.
        """
        if self.cfg.hnsw:
            faiss.ParameterSpace().set_index_parameter(self.index, "efSearch", efsearch)

    def normalize(self, vectors: np.ndarray) -> np.ndarray:
        """Normalize the input vectors for a backend library and the specified metric.

        Args:
            vectors (np.ndarray): Input vectors.

        Returns:
            np.ndarray: Normalized vectors.
        """
        if not np.issubdtype(vectors.dtype, np.float32):
            vectors = np.array(vectors, dtype=np.float32)
        if self.cfg.metric == "cos":
            vectors /= np.fmax(np.linalg.norm(vectors, axis=-1, keepdims=True), 1e-9)
        return vectors

    def train(self, vectors: np.ndarray) -> None:
        """Train the index for some approximate nearest neighbor search algorithms.

        Args:
            vectors (np.ndarray): Training vectors.
        """
        vectors = self.normalize(vectors)
        self.index.train(vectors)

    def add(self, vectors: np.ndarray, ids: Optional[np.ndarray] = None) -> None:
        """Add key vectors to the index.

        Args:
            vectors (np.ndarray): Key vectors to be added.
            ids (np.ndarray, optional): Value indices.
        """
        vectors = self.normalize(vectors)
        if ids is None:
            ids = np.arange(len(self), len(self) + len(vectors))
        return self.index.add_with_ids(vectors, ids)

    def search(self, querys: np.ndarray, k: int = 1) -> Tuple[np.ndarray, np.ndarray]:
        """Search the k nearest neighbor vectors of the querys.

        Args:
            querys (np.ndarray): Query vectors.
            k (int): Top-k.

        Returns:
            distances (np.ndarray): Distances between the querys and the k nearest
               neighbor vectors.
            indices (np.ndarray): Indices of the k nearest neighbor vectors.
        """
        querys = self.normalize(querys)
        return self.index.search(querys, k=k)

    @classmethod
    def load_index(cls, path: PathLike) -> Any:
        """Load the index.

        Args:
            path (os.PathLike): Index file path.

        Returns:
            faiss.Index: Index object.
        """
        return faiss.read_index(str(path))

    def save_index(self, path: PathLike) -> None:
        """Saves the index.

        Args:
            path (os.PathLike): Index file path to save.
        """
        return faiss.write_index(self.index, str(path))
