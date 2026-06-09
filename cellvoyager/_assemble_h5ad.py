#!/usr/bin/env python
"""Assemble an .h5ad from intermediate files exported by rds_to_h5ad.R.

Reads a manifest.json describing a temp directory of exported components
(sparse count matrix as MatrixMarket, cell/gene metadata as CSV, and reduced
dimension embeddings as CSV), builds an AnnData, and writes it to the output
path. Kept separate from the R side so the conversion never depends on
zellkonverter/basilisk provisioning its own Python environment.

Usage:
    python _assemble_h5ad.py <manifest.json> <output.h5ad>
"""
import json
import sys

import anndata as ad
import numpy as np
import pandas as pd
from scipy.io import mmread


def main(manifest_path: str, out_path: str) -> None:
    with open(manifest_path) as fh:
        m = json.load(fh)

    tmp = m["tmpdir"]

    # Counts: MatrixMarket is genes x cells; AnnData wants cells x genes.
    print(f"Reading matrix {m['matrix']} ...", flush=True)
    X = mmread(f"{tmp}/{m['matrix']}").tocsr().T.tocsr()

    print("Reading obs/var ...", flush=True)
    obs = pd.read_csv(f"{tmp}/{m['obs']}", index_col=0, low_memory=False)
    var = pd.read_csv(f"{tmp}/{m['var']}", index_col=0, low_memory=False)
    obs.index = obs.index.astype(str)
    var.index = var.index.astype(str)

    assert X.shape == (obs.shape[0], var.shape[0]), (
        f"shape mismatch: X={X.shape} obs={obs.shape[0]} var={var.shape[0]}"
    )

    adata = ad.AnnData(X=X, obs=obs, var=var)

    for key, fname in m.get("obsm", {}).items():
        arr = pd.read_csv(f"{tmp}/{fname}", index_col=0).to_numpy(dtype=np.float32)
        adata.obsm[key] = arr
        print(f"  added obsm[{key}] shape {arr.shape}", flush=True)

    print(f"Writing {out_path} ...", flush=True)
    adata.write_h5ad(out_path, compression="gzip")
    print(f"DONE. {adata.n_obs} cells x {adata.n_vars} genes -> {out_path}",
          flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("Usage: python _assemble_h5ad.py <manifest.json> <output.h5ad>")
    main(sys.argv[1], sys.argv[2])
