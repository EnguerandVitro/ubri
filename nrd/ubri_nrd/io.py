"""Écriture et lecture d'un jeu UBRI-NRD (partie meta/ et neural/). Toute écriture est atomique (KE#112)."""
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .schema import EPISODE_COLUMNS, EVENT_COLUMNS, PARTICIPANT_COLUMNS, VERSION


def _atomique(path: Path, write) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    write(tmp)
    os.replace(tmp, path)


def ecrire_json(path: Path, obj) -> None:
    _atomique(path, lambda p: p.write_text(json.dumps(obj, indent=2, ensure_ascii=False)))


def ecrire_parquet(path: Path, df: pd.DataFrame) -> None:
    _atomique(path, lambda p: pq.write_table(pa.Table.from_pandas(df, preserve_index=False), p))


def eeg_relpath(episode_index: int) -> str:
    return f"neural/eeg/chunk-{episode_index // 1000:03d}/file-{episode_index:06d}.parquet"


def ecrire_eeg(root: Path, episode_index: int, t: np.ndarray, data: np.ndarray, channels: list[str], *,
               idx: np.ndarray) -> str:
    """t (n,) en secondes sur l'horloge commune ; idx (n,) compteur d'échantillon de l'appareil (entier, un saut =
    un trou) ; data (n, n_canaux) en µV, dtype conservé (brut d'abord). idx est OBLIGATOIRE et sans défaut (KE#62) :
    un producteur qui l'oublie plante, il ne déclare jamais un flux continu par omission."""
    idx = np.asarray(idx)
    assert data.ndim == 2 and data.shape == (len(t), len(channels)) and idx.shape == (len(t),), \
        (data.shape, len(t), len(channels), idx.shape)
    assert np.issubdtype(idx.dtype, np.integer), f"idx doit être entier, reçu {idx.dtype}"
    cols = {"t": t.astype(np.float64), "idx": idx.astype(np.int64)}
    cols.update({c: data[:, i] for i, c in enumerate(channels)})
    rel = eeg_relpath(episode_index)
    ecrire_parquet(root / rel, pd.DataFrame(cols))
    return rel


def ecrire_meta(root: Path, info: dict, montage: dict, participants: pd.DataFrame, episodes: pd.DataFrame,
                events: pd.DataFrame) -> None:
    assert tuple(participants.columns) == PARTICIPANT_COLUMNS, participants.columns
    assert tuple(episodes.columns) == EPISODE_COLUMNS, episodes.columns
    episodes = episodes.astype({"idx_start": "int64", "idx_end": "int64", "pertes_annoncees": "Int64"})
    assert tuple(events.columns) == EVENT_COLUMNS, events.columns
    ecrire_parquet(root / "meta/participants.parquet", participants)
    ecrire_parquet(root / "neural/episodes.parquet", episodes)
    ecrire_parquet(root / "neural/events.parquet", events)
    ecrire_json(root / "meta/montage.json", montage)
    # info.json en DERNIER : un jeu sans info.json est un jeu incomplet, le validateur le refuse.
    ecrire_json(root / "meta/info.json", {"ubri_nrd": {"version": VERSION, **info}})


def lire_info(root: Path) -> dict:
    return json.loads((root / "meta/info.json").read_text())["ubri_nrd"]


def lire_montage(root: Path) -> dict:
    return json.loads((root / "meta/montage.json").read_text())


def lire_table(root: Path, rel: str) -> pd.DataFrame:
    return pq.read_table(root / rel).to_pandas()


def lire_eeg(root: Path, rel: str, channels: list[str]) -> tuple[np.ndarray, np.ndarray]:
    df = pq.read_table(root / rel).to_pandas()
    return df["t"].to_numpy(), df[channels].to_numpy()


def lire_eeg_grille(root: Path, episode, channels: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Grille RÉGULIÈRE de idx_start à idx_end pour un épisode (ligne de episodes.parquet) : (idx, t, x, present).
    Les échantillons manquants ont x = NaN et present = False ; leur t vient de la droite t ~ idx ajustée sur les
    échantillons présents (le dejitter du producteur). Lecture seule : le fichier, lui, reste brut."""
    df = pq.read_table(root / episode.eeg_file).to_pandas()
    i0, i1 = int(episode.idx_start), int(episode.idx_end)
    ii = df["idx"].to_numpy()
    # KE#113 : borner le domaine AVANT de s'en servir comme indice (un indice négatif écrirait en boucle, sans erreur)
    if ii.dtype != np.int64 or (len(ii) and (ii[0] < i0 or ii[-1] > i1 or not np.all(np.diff(ii) > 0))):
        raise ValueError(f"{episode.eeg_file} : idx hors de [{i0}, {i1}], non strictement croissant ou non int64 "
                         f"(valider le jeu d'abord)")
    grille = np.arange(i0, i1 + 1, dtype=np.int64)
    pos = ii - i0
    x = np.full((len(grille), len(channels)), np.nan)
    x[pos] = df[channels].to_numpy()
    present = np.zeros(len(grille), bool)
    present[pos] = True
    if len(df) >= 2:
        b, a = np.polyfit(df["idx"].to_numpy().astype(np.float64), df["t"].to_numpy(), 1)
        t = a + b * grille
        t[pos] = df["t"].to_numpy()
    else:
        t = np.full(len(grille), np.nan); t[pos] = df["t"].to_numpy()
    return grille, t, x, present
