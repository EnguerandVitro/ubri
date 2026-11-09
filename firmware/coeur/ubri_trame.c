/* Cœur logique du microprogramme de la carte UBRI v0. Voir ubri_trame.h. */
#include "ubri_trame.h"

#include <string.h>

#define SYNC0 0xA5u
#define SYNC1 0x5Au
#define VERSION_V3 3u
#define TYPE_OUVERTURE 0x81u
#define TYPE_CLOTURE2 0x83u /* clôture v2 ; la v1 (0x82) n'est plus émise */
#define N_LIBELLES 16u /* taille de LIBELLES dans trame.py : un code >= 16 est refusé (KE#113) */

static const uint8_t GAINS[] = {1, 2, 4, 6, 8, 12, 24}; /* gains programmables de l'ADS1299 (fiche) */

uint16_t ubri_crc16(const uint8_t *data, size_t n) {
    uint32_t crc = 0xFFFFu; /* calcul en 32 bits non signés : aucune promotion vers int signé (-Wconversion) */
    for (size_t i = 0; i < n; i++) {
        crc ^= (uint32_t)data[i] << 8;
        for (int b = 0; b < 8; b++)
            crc = ((crc & 0x8000u) ? ((crc << 1) ^ 0x1021u) : (crc << 1)) & 0xFFFFu;
    }
    return (uint16_t)crc;
}

static void be32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16); p[2] = (uint8_t)(v >> 8); p[3] = (uint8_t)v;
}

static void be16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)(v >> 8); p[1] = (uint8_t)v;
}

/* Scelle une trame : sortie[0..1] = synchro, CRC sur [2, L-2), écrit en fin. Retourne L. */
static int sceller(uint8_t *sortie, size_t corps_fin) {
    sortie[0] = SYNC0; sortie[1] = SYNC1;
    be16(sortie + corps_fin, ubri_crc16(sortie + 2, corps_fin - 2));
    return (int)(corps_fin + 2);
}

static int gain_valide(uint8_t g) {
    for (size_t i = 0; i < sizeof GAINS; i++)
        if (GAINS[i] == g) return 1;
    return 0;
}

int ubri_ouvrir(ubri_session *s, uint8_t *sortie, uint8_t n, const uint8_t *codes, uint32_t depart, uint16_t fs,
                uint8_t gain, uint32_t id_carte, uint16_t microprogramme, uint8_t bit_sync) {
    if (!s || !sortie || !codes || n < 1 || n > UBRI_N_MAX || n > UBRI_ADS_VOIES || fs == 0 || !gain_valide(gain) ||
        bit_sync > 3)
        return UBRI_ERR_ARGUMENT;
    for (uint8_t i = 0; i < n; i++) { /* codes dans la table et distincts, comme le décodeur l'exige */
        if (codes[i] >= N_LIBELLES) return UBRI_ERR_ARGUMENT;
        for (uint8_t j = 0; j < i; j++)
            if (codes[j] == codes[i]) return UBRI_ERR_ARGUMENT;
    }
    if (s->ouverte) return UBRI_ERR_SESSION;
    memset(s, 0, sizeof *s);
    s->ouverte = 1; s->n = n; s->bit_sync = bit_sync; s->depart = depart;
    s->dernier_front = UBRI_AUCUN_FRONT;
    uint8_t *p = sortie;
    p[2] = TYPE_OUVERTURE; p[3] = n;
    be32(p + 4, id_carte); be16(p + 8, microprogramme); be16(p + 10, fs); p[12] = gain; be32(p + 13, depart);
    memcpy(p + 17, codes, n);
    return sceller(sortie, 17u + n);
}

int ubri_echantillon(ubri_session *s, uint8_t *sortie, uint32_t timer, const uint8_t ads[UBRI_ADS_OCTETS],
                     uint8_t etat_carte) {
    if (!s || !sortie || !ads) return UBRI_ERR_ARGUMENT;
    if (!s->ouverte) return UBRI_ERR_SESSION;
    /* Mot d'état (fiche ADS1299) : 1100 | LOFF_STATP[7:0] | LOFF_STATN[7:0] | GPIO[7:4], soit 24 bits. */
    if ((ads[0] & 0xF0u) != 0xC0u) { s->refus++; return UBRI_ERR_ETAT_ADS; }
    if (timer == 0 || timer <= s->timer_prec) { s->incoherences_timer++; return UBRI_ERR_COMPTEUR; } /* pas une perte */
    uint8_t loff_p = (uint8_t)(((ads[0] & 0x0Fu) << 4) | (ads[1] >> 4));
    uint8_t loff_n = (uint8_t)(((ads[1] & 0x0Fu) << 4) | (ads[2] >> 4));
    uint8_t gpio = (uint8_t)(ads[2] & 0x0Fu);
    int niveau = (gpio >> s->bit_sync) & 1;

    /* DRDY non servies = sauts du TIMER : timer - timer_prec - 1 (au premier échantillon, timer - 1). */
    int premier = (s->timer_prec == 0u);
    uint32_t trou = timer - s->timer_prec - 1u; /* DRDY non servies juste avant celle-ci */
    s->drdy_manquees += trou;
    s->timer_prec = timer;
    uint32_t compteur = s->depart + timer - 1u;
    /* Un front n'est daté que si l'échantillon PRÉCÉDENT a été servi et lu bas (KE#289). Au 1er échantillon, ou
     * après un trou alors que le précédent servi était bas, l'instant du front est inconnu : pas d'ancre (elle
     * serait en retard de k × 4 ms et l'hôte l'accepterait à 1,5 échantillon près), on compte. Après un trou
     * alors que le précédent servi était haut, on suppose la même impulsion : vrai tant que l'intervalle BAS entre
     * deux impulsions dépasse la plus longue rafale de DRDY ratées (sinon deux impulsions fusionnent et un front
     * se perd sans être compté). */
    int incertain = niveau && (premier || (trou != 0u && !s->sync_prec));
    int front = niveau && !s->sync_prec && !incertain;
    if (incertain) s->fronts_incertains++;
    s->sync_prec = niveau;
    if (front) s->dernier_front = compteur;

    uint8_t *p = sortie;
    uint8_t n = s->n;
    p[2] = VERSION_V3; p[3] = n;
    be32(p + 4, compteur);
    memcpy(p + 8, ads + 3, (size_t)3 * n); /* voies : int24 gros-boutiste dans l'ADS1299 ET dans la trame */
    size_t ie = 8u + 3u * n;
    p[ie] = (uint8_t)(front ? 1u : 0u);  /* bit 0 = front de synchro ; bits 1..7 réservés, à 0 */
    be32(p + ie + 1, s->dernier_front);
    p[ie + 5] = loff_p; p[ie + 6] = loff_n; p[ie + 7] = etat_carte;
    return sceller(sortie, ie + 8);
}

void ubri_abandon(ubri_session *s, uint32_t n) {
    if (s && s->ouverte) s->abandonnees += n;
}

int ubri_timer_coherent(ubri_session *s, uint32_t timer) {
    if (!s || !s->ouverte) return 0;
    if (timer == 0 || timer <= s->timer_prec) { s->incoherences_timer++; return 0; }
    return 1;
}

void ubri_lecture_jetee(ubri_session *s) {
    if (s && s->ouverte) s->lectures_jetees++;
}

void ubri_refus_lecture(ubri_session *s) {
    if (s && s->ouverte) s->refus++;
}

int ubri_cloturer(ubri_session *s, uint8_t *sortie, uint32_t timer_final) {
    if (!s || !sortie) return UBRI_ERR_ARGUMENT;
    if (!s->ouverte) return UBRI_ERR_SESSION;
    if (timer_final < s->timer_prec) return UBRI_ERR_COMPTEUR; /* 0 admis : session sans aucune DRDY */
    s->drdy_manquees += timer_final - s->timer_prec; /* DRDY de queue jamais servies */
    s->timer_prec = timer_final;
    uint8_t *p = sortie;
    p[2] = TYPE_CLOTURE2; p[3] = s->n;
    be32(p + 4, s->depart + timer_final - 1u);
    be32(p + 8, s->drdy_manquees);
    be32(p + 12, s->abandonnees);
    be32(p + 16, s->fronts_incertains);
    be32(p + 20, s->lectures_jetees);
    be32(p + 24, s->refus);
    be32(p + 28, s->incoherences_timer);
    s->ouverte = 0;
    return sceller(sortie, 32);
}
