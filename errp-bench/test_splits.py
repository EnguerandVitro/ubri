"""Test de leave_one_subject_out. Lancer : python3 -B test_splits.py"""
import numpy as np
from splits import leave_one_subject_out

subj = np.array([3, 1, 1, 2, 3, 2, 1, 3])
folds = leave_one_subject_out(subj)

# Témoin positif en égalité exacte (KE#121) : on connaît la réponse attendue.
attendu = {
    1: ([0, 3, 4, 5, 7], [1, 2, 6]),
    2: ([0, 1, 2, 4, 6, 7], [3, 5]),
    3: ([1, 2, 3, 5, 6], [0, 4, 7]),
}
assert len(folds) == 3, f"un pli par sujet attendu, reçu {len(folds)}"
for s, (tr, te) in zip(sorted(attendu), folds):
    assert sorted(tr.tolist()) == attendu[s][0], f"sujet {s} : entraînement faux {tr}"
    assert sorted(te.tolist()) == attendu[s][1], f"sujet {s} : test faux {te}"
    # Invariant : aucune fuite du sujet testé dans l'entraînement, et couverture complète.
    assert not set(subj[tr]) & set(subj[te]), f"fuite du sujet {s}"
    assert len(tr) + len(te) == len(subj)
print("OK : 3 plis, aucune fuite, couverture complète")
