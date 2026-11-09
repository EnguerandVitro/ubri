/* Tests du cœur du microprogramme sur PC. Compiler et lancer : make -C products/ubri/firmware/coeur test
 * 1. vecteurs de référence FIGÉS, calculés par l'encodeur Python (trame.py) : le C doit les reproduire à l'octet ;
 * 2. refus nommés (mot d'état corrompu, TIMER qui recule, gain, codes, session) ;
 * 3. mode « scénario » (argv[1] = fichier) : lit un scénario ligne à ligne, écrit chaque trame en hexadécimal,
 *    pour la parité avec Python (tests/parite.py). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../ubri_trame.h"

static int echecs = 0;
#define VERIFIE(cond, msg)                                       \
    do {                                                         \
        if (!(cond)) { printf("KO  %s\n", msg); echecs++; }      \
        else printf("OK  %s\n", msg);                            \
    } while (0)

static void hex(const uint8_t *b, int n, char *out) {
    for (int i = 0; i < n; i++) sprintf(out + 2 * i, "%02x", b[i]);
    out[2 * n] = 0;
}

static void ads_brut(uint8_t ads[UBRI_ADS_OCTETS], const int32_t v[8], uint8_t loff_p, uint8_t loff_n, uint8_t gpio) {
    ads[0] = (uint8_t)(0xC0u | (loff_p >> 4));
    ads[1] = (uint8_t)(((loff_p & 0x0Fu) << 4) | (loff_n >> 4));
    ads[2] = (uint8_t)(((loff_n & 0x0Fu) << 4) | (gpio & 0x0Fu));
    for (int i = 0; i < 8; i++) {
        uint32_t u = (uint32_t)v[i] & 0xFFFFFFu;
        ads[3 + 3 * i] = (uint8_t)(u >> 16); ads[4 + 3 * i] = (uint8_t)(u >> 8); ads[5 + 3 * i] = (uint8_t)u;
    }
}

static const uint8_t CODES[8] = {0, 1, 2, 3, 4, 5, 6, 7}; /* Fz FCz Cz CPz Pz C3 C4 EOG */

static void vecteurs(void) {
    uint8_t out[UBRI_TRAME_V3_MAX]; char h[200];
    ubri_session s; memset(&s, 0, sizeof s);
    VERIFIE(ubri_crc16((const uint8_t *)"123456789", 9) == 0x29B1u, "CRC-16/CCITT-FALSE : 0x29B1 sur « 123456789 »");
    int n = ubri_ouvrir(&s, out, 8, CODES, 1000, 250, 24, 0x0B1, 3, 0);
    hex(out, n, h);
    VERIFIE(n == 27 && !strcmp(h, "a55a8108000000b1000300fa18000003e800010203040506074886"), "OUVERTURE identique à Python");
    const int32_t v[8] = {1, -1, 8388607, -8388608, 0, 123456, -654321, 42};
    uint8_t ads[UBRI_ADS_OCTETS];
    uint8_t ads_bas[UBRI_ADS_OCTETS] = {0xC0, 0, 0};
    VERIFIE(ubri_echantillon(&s, out, 4, ads_bas, 0) == 42, "1er échantillon au TIMER 4, synchro basse : 3 DRDY manquées");
    ads_brut(ads, v, 0x81, 0x24, 0x1);
    n = ubri_echantillon(&s, out, 5, ads, 200); /* TIMER 5, précédent servi et bas : front daté au compteur 1004 */
    hex(out, n, h);
    VERIFIE(n == 42 && !strcmp(h, "a55a0308000003ec000001ffffff7fffff80000000000001e240f6040f00002a01000003ec8124c88380"),
            "trame v3 identique à Python (bornes int24, front, lead-off, état)");
    ubri_abandon(&s, 20);
    n = ubri_cloturer(&s, out, 9); /* fin au TIMER 9 : 4 DRDY de queue jamais servies, 3 + 4 = 7 en tout */
    hex(out, n, h);
    VERIFIE(n == 34 && !strcmp(h, "a55a8308000003f00000000700000014000000000000000000000000000000002440"),
            "CLÔTURE v2 identique à Python (7 DRDY, 20 abandons, 0 incertain, 0 jetée, 0 refus, 0 incohérence)");
}

static void session_vide_et_parseur(void) {
    uint8_t out[UBRI_TRAME_V3_MAX]; char h[200];
    ubri_session s; memset(&s, 0, sizeof s);
    VERIFIE(ubri_ouvrir(&s, out, 8, CODES, 1000, 250, 24, 0x0B1, 3, 0) == 27, "ouverture (session vide)");
    int n = ubri_cloturer(&s, out, 0); /* fermée avant la 1re DRDY (BLE coupé, START raté) */
    hex(out, n, h);
    VERIFIE(n == 34 && !strcmp(h, "a55a8308000003e7000000000000000000000000000000000000000000000000a3a0"),
            "clôture d'une session vide (TIMER 0) identique à Python, 0 perte");
    VERIFIE(ubri_ouvrir(&s, out, 8, CODES, 1000, 250, 24, 0x0B1, 3, 0) == 27, "réouverture après une session vide");
    /* Parseur du mot d'état contre des octets LITTÉRAUX (pas l'inverse écrit de la même main) :
     * C8 12 41 = 1100 | 1000 0001 (LOFF_STATP 0x81) | 0010 0100 (LOFF_STATN 0x24) | 0001 (GPIO1). */
    uint8_t ads[UBRI_ADS_OCTETS] = {0xC8, 0x12, 0x41};
    n = ubri_echantillon(&s, out, 1, ads, 0);
    VERIFIE(n == 42 && out[32] == 0 && out[37] == 0x81 && out[38] == 0x24,
            "mot d'état littéral C8 12 41 : lead-off P 0x81, N 0x24 ; GPIO1 haut au 1er échantillon : PAS de front");
    VERIFIE(s.fronts_incertains == 1, "niveau haut au 1er échantillon : 1 front incertain compté");
}

static void fronts(void) {
    uint8_t out[UBRI_TRAME_V3_MAX];
    uint8_t bas[UBRI_ADS_OCTETS] = {0xC0, 0, 0}, haut[UBRI_ADS_OCTETS] = {0xC0, 0, 0x01};
    ubri_session s; memset(&s, 0, sizeof s);
    ubri_ouvrir(&s, out, 8, CODES, 0, 250, 24, 0, 0, 0);
    ubri_echantillon(&s, out, 1, bas, 0);
    ubri_echantillon(&s, out, 2, bas, 0);
    int n = ubri_echantillon(&s, out, 5, haut, 0); /* trou de 2 DRDY (3, 4) juste avant le niveau haut */
    VERIFIE(n == 42 && out[32] == 0 && s.dernier_front == UBRI_AUCUN_FRONT && s.fronts_incertains == 1,
            "trou juste avant le front : front NON daté (pas d'ancre en retard), 1 incertain");
    ubri_echantillon(&s, out, 6, haut, 0);
    VERIFIE(out[32] == 0 && s.fronts_incertains == 1, "même impulsion, échantillon suivant : ni front ni incertain");
    ubri_echantillon(&s, out, 7, bas, 0);
    ubri_echantillon(&s, out, 8, haut, 0); /* précédent servi et bas : front daté au compteur 7 (départ 0) */
    VERIFIE(out[32] == 1 && s.dernier_front == 7u, "front normal : daté au compteur exact (7)");
    ubri_echantillon(&s, out, 9, bas, 0);
    ubri_echantillon(&s, out, 12, haut, 0); /* trou mais le précédent servi était bas : incertain */
    ubri_echantillon(&s, out, 13, haut, 0);
    ubri_echantillon(&s, out, 15, haut, 0); /* trou alors que le précédent servi était haut : même impulsion */
    VERIFIE(s.fronts_incertains == 2 && s.dernier_front == 7u,
            "trou dans une impulsion déjà haute : pas de nouvel incertain ; dernier front inchangé (7)");
}

static void refus(void) {
    uint8_t out[UBRI_TRAME_V3_MAX]; uint8_t ads[UBRI_ADS_OCTETS]; const int32_t z[8] = {0};
    ubri_session s; memset(&s, 0, sizeof s);
    ads_brut(ads, z, 0, 0, 0);
    VERIFIE(ubri_echantillon(&s, out, 1, ads, 0) == UBRI_ERR_SESSION, "échantillon avant l'ouverture : refusé");
    VERIFIE(ubri_ouvrir(&s, out, 8, CODES, 0, 250, 3, 0, 0, 0) == UBRI_ERR_ARGUMENT, "gain 3 (hors table) : refusé");
    const uint8_t doublon[2] = {0, 0};
    VERIFIE(ubri_ouvrir(&s, out, 2, doublon, 0, 250, 24, 0, 0, 0) == UBRI_ERR_ARGUMENT, "code de voie en double : refusé");
    const uint8_t hors[1] = {16};
    VERIFIE(ubri_ouvrir(&s, out, 1, hors, 0, 250, 24, 0, 0, 0) == UBRI_ERR_ARGUMENT, "code de voie 16 (hors table) : refusé");
    VERIFIE(ubri_ouvrir(&s, out, 9, CODES, 0, 250, 24, 0, 0, 0) == UBRI_ERR_ARGUMENT, "9 voies (> 8 de l'ADS1299) : refusé");
    VERIFIE(ubri_ouvrir(&s, out, 8, CODES, 0, 250, 24, 0, 0, 0) == 27, "ouverture valide");
    VERIFIE(ubri_ouvrir(&s, out, 8, CODES, 0, 250, 24, 0, 0, 0) == UBRI_ERR_SESSION, "deuxième ouverture : refusée");
    uint8_t casse[UBRI_ADS_OCTETS]; memcpy(casse, ads, sizeof casse); casse[0] = 0x40;
    VERIFIE(ubri_echantillon(&s, out, 1, casse, 0) == UBRI_ERR_ETAT_ADS, "mot d'état sans en-tête 1100 : refusé");
    VERIFIE(ubri_echantillon(&s, out, 0, ads, 0) == UBRI_ERR_COMPTEUR, "TIMER à 0 : refusé");
    VERIFIE(ubri_echantillon(&s, out, 3, ads, 0) == 42, "échantillon au TIMER 3");
    VERIFIE(ubri_echantillon(&s, out, 3, ads, 0) == UBRI_ERR_COMPTEUR, "TIMER qui ne progresse pas : refusé");
    VERIFIE(ubri_echantillon(&s, out, 2, ads, 0) == UBRI_ERR_COMPTEUR, "TIMER qui recule : refusé");
    VERIFIE(s.drdy_manquees == 2, "un refus ne compte aucune DRDY : 2 DRDY manquées (TIMER 3)");
    VERIFIE(s.refus == 1 && s.incoherences_timer == 3,
            "mot d'état corrompu = 1 refus ; TIMER 0, figé, qui recule = 3 incohérences, PAS des refus (KE#300)");
    ubri_lecture_jetee(&s); ubri_refus_lecture(&s);
    VERIFIE(s.lectures_jetees == 1 && s.refus == 2, "lecture jetée et échec SPI signalés par le pilote : comptés");
    VERIFIE(ubri_cloturer(&s, out, 2) == UBRI_ERR_COMPTEUR, "clôture avant le dernier échantillon : refusée");
    VERIFIE(ubri_cloturer(&s, out, 3) == 34, "clôture v2 valide");
    VERIFIE(out[2] == 0x83 && out[23] == 1 && out[27] == 2 && out[31] == 3,
            "clôture v2 : 1 jetée, 2 refus, 3 incohérences (une CLÔTURE refusée n'est pas comptée)");
    VERIFIE(ubri_echantillon(&s, out, 4, ads, 0) == UBRI_ERR_SESSION, "échantillon après la clôture : refusé");
}

static void timer_qui_repart(void) {
    uint8_t out[UBRI_TRAME_V3_MAX]; uint8_t ads[UBRI_ADS_OCTETS] = {0xC0, 0, 0};
    ubri_session s; memset(&s, 0, sizeof s);
    ubri_ouvrir(&s, out, 8, CODES, 0, 250, 24, 0, 0, 0);
    ubri_echantillon(&s, out, 5, ads, 0);
    uint32_t avant = s.drdy_manquees;
    VERIFIE(ubri_echantillon(&s, out, 2, ads, 0) == UBRI_ERR_COMPTEUR &&
            ubri_echantillon(&s, out, 3, ads, 0) == UBRI_ERR_COMPTEUR, "TIMER 5 -> 2 -> 3 : refusés");
    VERIFIE(ubri_echantillon(&s, out, 6, ads, 0) == 42, "TIMER 6 : servi");
    VERIFIE(s.incoherences_timer == 2 && s.refus == 0 && s.drdy_manquees == avant,
            "TIMER qui repart : 2 incohérences, 0 refus, AUCUNE DRDY manquée ajoutée (6 - 5 - 1 = 0)");
    VERIFIE(s.lectures_jetees + s.refus <= s.drdy_manquees, "invariant jetées + refus <= DRDY manquées");
    VERIFIE(ubri_timer_coherent(&s, 6) == 0 && s.incoherences_timer == 3,
            "interruption en retard (TIMER 6 déjà servi) : pas de lecture, 1 incohérence de plus");
    VERIFIE(ubri_timer_coherent(&s, 7) == 1 && s.incoherences_timer == 3, "TIMER 7 nouveau : lecture permise");
    ubri_session ferme; memset(&ferme, 0, sizeof ferme);
    VERIFIE(ubri_timer_coherent(&ferme, 9) == 0 && ferme.incoherences_timer == 0, "hors session : 0, rien compté");
}

/* Scénario : lignes « O depart bit_sync », « E timer etat loff_p loff_n gpio v0..v7 », « A n », « C timer_final »,
 * « J » (lecture jetée par le pilote), « R » (échec SPI), « X timer » (mot d'état corrompu à ce TIMER),
 * « Z timer » (interruption en retard : échantillon valide à un TIMER déjà servi, ERR_COMPTEUR).
 * Sortie : une ligne hexadécimale par trame écrite, ou « ERR code » si la fonction refuse. */
static int scenario(const char *chemin) {
    FILE *f = fopen(chemin, "r");
    if (!f) { perror(chemin); return 2; }
    ubri_session s; memset(&s, 0, sizeof s);
    uint8_t out[UBRI_TRAME_V3_MAX]; char h[200]; char t;
    while (fscanf(f, " %c", &t) == 1) {
        int n = 0;
        if (t == 'O') {
            unsigned depart, bit; if (fscanf(f, "%u %u", &depart, &bit) != 2) return 3;
            n = ubri_ouvrir(&s, out, 8, CODES, depart, 250, 24, 0x0B1, 3, (uint8_t)bit);
        } else if (t == 'E') {
            unsigned timer, etat, lp, ln, gpio; int v[8];
            if (fscanf(f, "%u %u %u %u %u", &timer, &etat, &lp, &ln, &gpio) != 5) return 3;
            for (int i = 0; i < 8; i++) if (fscanf(f, "%d", &v[i]) != 1) return 3;
            uint8_t ads[UBRI_ADS_OCTETS]; int32_t vv[8];
            for (int i = 0; i < 8; i++) vv[i] = v[i];
            ads_brut(ads, vv, (uint8_t)lp, (uint8_t)ln, (uint8_t)gpio);
            n = ubri_echantillon(&s, out, timer, ads, (uint8_t)etat);
        } else if (t == 'A') {
            unsigned k; if (fscanf(f, "%u", &k) != 1) return 3;
            ubri_abandon(&s, k); continue;
        } else if (t == 'J') {
            ubri_lecture_jetee(&s); continue;
        } else if (t == 'R') {
            ubri_refus_lecture(&s); continue;
        } else if (t == 'X') {
            unsigned timer; if (fscanf(f, "%u", &timer) != 1) return 3;
            uint8_t ads[UBRI_ADS_OCTETS] = {0x40, 0, 0}; /* en-tête 0100 au lieu de 1100 : lecture corrompue */
            n = ubri_echantillon(&s, out, timer, ads, 0);
        } else if (t == 'Z') {
            unsigned timer; if (fscanf(f, "%u", &timer) != 1) return 3;
            uint8_t ads[UBRI_ADS_OCTETS] = {0xC0, 0, 0};
            n = ubri_echantillon(&s, out, timer, ads, 0);
        } else if (t == 'C') {
            unsigned tf; if (fscanf(f, "%u", &tf) != 1) return 3;
            n = ubri_cloturer(&s, out, tf);
        } else return 3;
        if (n < 0) { printf("ERR %d\n", n); continue; }
        hex(out, n, h); printf("%s\n", h);
    }
    fclose(f);
    printf("I %u\n", (unsigned)s.fronts_incertains); /* dernière ligne : fronts incertains (non transmis) */
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 2) return scenario(argv[1]);
    vecteurs();
    session_vide_et_parseur();
    fronts();
    refus();
    timer_qui_repart();
    printf(echecs ? "ÉCHECS : %d\n" : "TOUT VERT\n", echecs);
    return echecs ? 1 : 0;
}
