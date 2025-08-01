import os
import logging
import joblib
import ngtpy as ngt
import numpy as np
import pandas as pd
from sklearn import preprocessing
from sklearn.decomposition import PCA

def build_indexed_sample(
    data_bank,
    lc_features=[],
    host_features=[],
    use_pca=False,
    num_pca_components=None,
    #num_trees=1000,
    path_to_index_directory="",
    save=True,
    force_recreation_of_index=True,
    weight_lc_feats_factor=1.0,
    random_seed=42
):
    """Create (or load) an NGT index over a reference feature bank.

    This function builds or loads an NGT index for fast similarity search over a dataset
    of astronomical transients. It can optionally apply PCA for dimensionality reduction
    and weight lightcurve features more heavily than host features.

    Parameters
    ----------
    data_bank : pandas.DataFrame
        DataFrame containing feature data with ztf_object_id as index.
    lc_features : list[str], default []
        Names of lightcurve feature columns to include.
    host_features : list[str], default []
        Names of host galaxy feature columns to include.
    use_pca : bool, default False
        Whether to apply PCA before indexing.
    num_pca_components : int | None, default None
        Number of PCA components to use. If None and use_pca=True,
        keeps 99% of variance.
    #num_trees : int, default 1000
    #    Number of random projection trees for NGT index.
    path_to_index_directory : str | Path, default ""
        Directory to save index and support files.
    save : bool, default True
        Whether to save the index and support files to disk.
    force_recreation_of_index : bool, default True
        Whether to rebuild the index even if it already exists.
    weight_lc_feats_factor : float, default 1.0
        Factor to up-weight lightcurve features relative to host features.
        Ignored if use_pca=True.

    Returns
    -------
    tuple
        (index_stem_name_with_path, scaler, feat_arr_scaled)
        - index_stem_name_with_path: Path to index files (without extension)
        - scaler: Fitted StandardScaler instance
        - feat_arr_scaled: Scaled feature array

    Raises
    ------
    ValueError
        If no features are provided
        If required columns are missing from data_bank
        If data contains NaN or infinite values after scaling

    Notes
    -----
    The function saves several files:
    - {index_stem_name_with_path}.ngt: NGT index file
    - {index_stem_name_with_path}_idx_arr.npy: Array of ZTF IDs
    - {index_stem_name_with_path}_feat_arr.npy: Original feature array
    - {index_stem_name_with_path}_scaler.joblib: Fitted scaler
    - {index_stem_name_with_path}_feat_arr_scaled.npy: Scaled features
    - {index_stem_name_with_path}_pca.joblib: PCA model (if use_pca=True)
    - {index_stem_name_with_path}_feat_arr_scaled_pca.npy: PCA-transformed features
    """

    # Confirm that the first column is the ZTF ID, and index by ZTF ID
    if "ztf_object_id" not in data_bank.columns.values:
        if "ZTFID" in data_bank.columns.values:
            data_bank = data_bank.rename(columns={'ZTFID': 'ztf_object_id'})
        else:
            raise ValueError(
                f"Error: Expected 'ztf_object_id' or 'ZTFID' column in dataset bank, but got '{data_bank.columns[0]}' instead."
            )
    data_bank = data_bank.set_index("ztf_object_id")

    # Ensure proper user input of features
    num_lc_features   = len(lc_features)
    num_host_features = len(host_features)

    if num_lc_features + num_host_features == 0:
        raise ValueError("Error: must provide at least one lightcurve or host feature.")
    if num_lc_features == 0:
        print(
            f"No lightcurve features provided. Running host-only LAISS with {num_host_features} features."
        )
    if num_host_features == 0:
        print(
            f"No host features provided. Running lightcurve-only LAISS with {num_lc_features} features."
        )

    # Filtering dataset bank for provided features
    data_bank = data_bank[lc_features + host_features]

    # Scale dataset bank features
    feat_arr = np.array(data_bank)
    idx_arr = np.array(data_bank.index) # Use index from cleaned data

    scaler = preprocessing.StandardScaler()
    scaler = scaler.fit(feat_arr)
    feat_arr_scaled = scaler.transform(feat_arr)

    if not use_pca:
        # Upweight lightcurve features
        weighted_feat_arr = feat_arr_scaled.copy()
        weighted_feat_arr[:, :num_lc_features] *= weight_lc_feats_factor
    else:
        pcaModel = PCA(n_components=num_pca_components, random_state=random_seed)
        feat_arr_scaled_pca = pcaModel.fit_transform(feat_arr_scaled)

    # Save PCA and non-PCA index arrays to binary files
    os.makedirs(path_to_index_directory, exist_ok=True)
    index_stem_name = (
        f"re_laiss_ngt_index_pca{use_pca}"
        + (f"_{num_pca_components}comps" if use_pca else "")
        + f"_{num_lc_features}lc_{num_host_features}host"
        + f"_{int(weight_lc_feats_factor)}weight"
    )
    index_stem_name_with_path = path_to_index_directory + "/" + index_stem_name
    if save:
        np.save(f"{index_stem_name_with_path}_idx_arr.npy", idx_arr)
        np.save(f"{index_stem_name_with_path}_feat_arr.npy", feat_arr)

        # Save the scaler
        joblib.dump(scaler, f"{index_stem_name_with_path}_scaler.joblib")

        if use_pca:
            np.save(
                f"{index_stem_name_with_path}_feat_arr_scaled_pca.npy",
                feat_arr_scaled_pca,
            )
            # Save the PCA model
            joblib.dump(pcaModel, f"{index_stem_name_with_path}_pca.joblib")

        else:
            np.save(
                f"{index_stem_name_with_path}_feat_arr_scaled.npy",
                feat_arr_scaled,
            )

            # Save the scaled and weighted feature array used to build the index
            np.save(
                f"{index_stem_name_with_path}_feat_arr_scaled_weighted.npy",
                weighted_feat_arr,
            )

    # Create or load the NGT index:
    index_dir = f"{index_stem_name_with_path}.ngt"
    index_dim = feat_arr_scaled_pca.shape[1] if use_pca else feat_arr_scaled.shape[1]
    
    if os.path.exists(index_dir) and not force_recreation_of_index:
        print("Loading previously saved NGT index...")
        index = ngt.Index(index_dir.encode())
        idx_arr = np.load(
            f"{index_stem_name_with_path}_idx_arr.npy",
            allow_pickle=True,
        )
    else:
        print(f"Building new NGT index with {data_bank.shape[0]} transients...")

        ngt.create(index_dir.encode(), index_dim, distance_type="L2")
        index = ngt.Index(index_dir.encode())

        if use_pca:
            index.batch_insert(feat_arr_scaled_pca)
        else:
            index.batch_insert(weighted_feat_arr)
        if save:
            index.save()

    print("Done!\n")

    return (
        index_stem_name_with_path,
        scaler,
        feat_arr_scaled_pca if use_pca else feat_arr_scaled
    )
