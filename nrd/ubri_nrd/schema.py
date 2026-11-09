"""Vocabulaire FERMÉ du format (KE#49) : tout type ou champ inconnu est une erreur de validation, jamais un défaut."""
VERSION = "0.2"  # v0.2 : trous dans l'EEG (colonne idx, idx_start/idx_end/pertes_annoncees)

AGENTS = frozenset({"robot", "curseur_ecran"})

# type d'événement -> champs obligatoires dans `details` (en plus de t, episode_index, type)
EVENT_TYPES = {
    "robot_action_start": {"action_id"},
    "robot_action_end": {"action_id"},
    "robot_error_injected": {"error_kind", "planned"},
    "robot_correct": set(),
    "stimulus": {"target"},
    "participant_report": {"saw_error"},
    "artifact": {"source"},
    "source_event": set(),  # événement du jeu d'origine non interprété ; source_code obligatoire
}
# champs FACULTATIFS déclarés par type : autorisés, jamais exigés. Tout champ hors (obligatoires ∪ facultatifs) est refusé.
EVENT_OPTIONAL = {
    "robot_correct": {"action_id"},
    "robot_error_injected": {"action_id"},
}
assert set(EVENT_OPTIONAL) <= set(EVENT_TYPES)
ERROR_KINDS = frozenset({"mauvais_objet", "mauvaise_place", "trajectoire", "mauvaise_direction"})

CONSENT_FIELDS = ("consent_collecte", "consent_publication", "consent_usage_commercial")
PARTICIPANT_COLUMNS = ("participant_id", "age_band", "handedness", *CONSENT_FIELDS, "source")
# n_samples = échantillons PRÉSENTS dans le fichier ; idx_start..idx_end = étendue ANNONCÉE par la source (compteur
# d'échantillon de l'appareil) ; t_start / t_end = heure de idx_start / idx_end (même si ces échantillons manquent) ;
# pertes_annoncees = pertes déclarées par la SOURCE (clôture de session), entier Int64 nullable (jamais un flottant),
# vide si la source n'annonce rien. JAMAIS calculé par l'hôte à partir des compteurs reçus : ce serait l'identité
# de KE#286 (manquants = manquants) et le contrôle des pertes côté hôte ne verrait plus rien.
EPISODE_COLUMNS = ("episode_index", "participant_id", "session", "run", "t_start", "t_end", "n_samples", "eeg_file",
                   "idx_start", "idx_end", "pertes_annoncees")
EEG_COLONNES_FIXES = ("t", "idx")  # puis les voies du montage, dans l'ordre
EVENT_COLUMNS = ("t", "episode_index", "type", "details", "source_code")
INFO_KEYS = ("version", "agent", "eeg_device", "fs", "unit", "clock_sync", "source", "licence")
