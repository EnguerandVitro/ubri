"""Découpage entraînement / test du banc ErrP (BNCI 2015-013, 6 sujets × 2 sessions).

Question mesurée : l'ErrP se détecte-t-il sur une personne que le modèle n'a JAMAIS vue ?
Si un seul essai du sujet testé fuit dans l'entraînement, le score mesure la calibration,
pas la généralisation, et toute la thèse « peu de calibration » est jugée sur un faux chiffre.
"""
import numpy as np


def leave_one_subject_out(subject_of_trial: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """Un pli par sujet : on teste sur ce sujet, on entraîne sur tous les autres.

    subject_of_trial : tableau (n_essais,), le numéro de sujet de chaque essai (ex. [1,1,1,2,2,...]).
    Retour : liste de (indices_entrainement, indices_test), un tuple par sujet, dans l'ordre croissant des sujets.

    Test : `python3 -B test_splits.py`.
    """
    subject_of_trial = np.asarray(subject_of_trial)
    folds = []
    for s in np.unique(subject_of_trial):  # np.unique trie : ordre croissant des sujets
        test = subject_of_trial == s
        folds.append((np.where(~test)[0], np.where(test)[0]))
    return folds
