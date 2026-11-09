/* Cœur logique du microprogramme de la carte UBRI v0 (cahier vault/01-projects/ubri/hardware/carte-ubri-v0.md).
 *
 * C99 portable, sans allocation ni dépendance : compilé tel quel sur le nRF52840 (Zephyr) et sur PC pour les tests.
 * Ne touche à aucun matériel : le pilote lit le SPI (27 octets de l'ADS1299) et le TIMER (nombre de DRDY depuis le
 * début de session), puis appelle ces fonctions qui décident et encodent.
 *
 * Format des trames : identique, octet pour octet, à products/ubri/connect/ubri_connect/trame.py (version 3,
 * OUVERTURE 0x81, CLÔTURE v2 0x83 ; la clôture 0x82 n'est plus émise, l'hôte la décode encore). La parité est figée
 * par des vecteurs de référence partagés (KE#40).
 */
#ifndef UBRI_TRAME_H
#define UBRI_TRAME_H

#include <stddef.h>
#include <stdint.h>

#define UBRI_N_MAX 16
#define UBRI_ADS_VOIES 8
#define UBRI_ADS_OCTETS (3 + 3 * UBRI_ADS_VOIES)   /* mot d'état 24 bits + 8 voies de 24 bits = 27 */
#define UBRI_TRAME_V3_MAX (18 + 3 * UBRI_N_MAX)
#define UBRI_OUVERTURE_MAX (19 + UBRI_N_MAX)
#define UBRI_CLOTURE_LONGUEUR 34 /* clôture v2 (0x83) */
#define UBRI_AUCUN_FRONT 0xFFFFFFFFu

/* Codes de retour (négatifs = refus, rien n'est écrit dans la sortie). */
#define UBRI_OK 0
#define UBRI_ERR_ARGUMENT (-1)       /* pointeur nul, n hors [1, N_MAX], gain hors table, code de voie hors table */
#define UBRI_ERR_ETAT_ADS (-2)       /* les 4 bits de tête du mot d'état ne valent pas 1100 : lecture SPI corrompue */
#define UBRI_ERR_COMPTEUR (-3)       /* TIMER qui recule ou repart à zéro en cours de session */
#define UBRI_ERR_SESSION (-4)        /* appel hors session (pas d'ouverture, ou après la clôture) */

/* CRC-16/CCITT-FALSE (polynôme 0x1021, init 0xFFFF), égal à binascii.crc_hqx(data, 0xFFFF) côté Python. */
uint16_t ubri_crc16(const uint8_t *data, size_t n);

/* État de session tenu par le microprogramme. Tout est à zéro avant ubri_ouvrir(). */
typedef struct {
    int ouverte;            /* 1 entre ubri_ouvrir et ubri_cloturer */
    uint8_t n;              /* voies émises (les n premières de l'ADS1299) */
    uint8_t bit_sync;       /* bit GPIO de l'ADS1299 (0..3) qui porte l'entrée de synchronisation */
    uint32_t depart;        /* compteur du premier échantillon, annoncé dans l'ouverture */
    uint32_t timer_prec;    /* valeur du TIMER au dernier échantillon servi (0 = aucun) */
    uint32_t drdy_manquees; /* DRDY non servies, vues comme des sauts du TIMER */
    uint32_t abandonnees;   /* trames abandonnées par la carte (tampon radio plein) */
    uint32_t dernier_front; /* compteur du dernier front montant de synchro, ou UBRI_AUCUN_FRONT */
    int sync_prec;          /* niveau de synchro de l'échantillon servi précédent */
    uint32_t fronts_incertains; /* niveau haut vu au 1er échantillon ou juste après un trou, alors que l'échantillon
                                 * servi précédent était bas : front réel, instant inconnu, NON daté (KE#289).
                                 * Transmis dans la clôture v2 ; côté hôte, l'événement apparaît comme « ancre
                                 * perdue ». */
    uint32_t lectures_jetees;   /* lectures SPI jetées par le pilote (TIMER changé pendant la lecture) */
    uint32_t refus;             /* lectures refusées en session : mot d'état corrompu (compté ici par
                                 * ubri_echantillon) ou échec SPI (signalé par le pilote). Une lecture jetée ou un
                                 * refus laisse UNE DRDY non servie, aussi comptée dans drdy_manquees : invariant
                                 * lectures_jetees + refus <= drdy_manquees (ils EXPLIQUENT des pertes). */
    uint32_t incoherences_timer; /* TIMER figé, qui recule ou repart à zéro (ERR_COMPTEUR) : PAS une perte, hors de
                                  * l'invariant, peut dépasser drdy_manquees (KE#300). fronts_incertains ne mesure
                                  * aucune perte non plus (front au 1er échantillon). */
} ubri_session;

/* CONCURRENCE : toutes les fonctions ubri_* d'une même session s'appellent depuis UN SEUL contexte (le fil
 * d'acquisition). Le fil BLE ne touche jamais `s` : il poste ses abandons (k_msgq ou atomic_t lu par le fil
 * d'acquisition), ou c'est le producteur qui appelle ubri_abandon quand l'enfilage dans le tampon radio échoue.
 * Synchro : ligne ACTIVE HAUTE (niveau bas au repos), bit_sync 0..3 = GPIO1..GPIO4 de l'ADS1299 (supposé : la
 * fiche dit « bits[4:7] of the GPIO register », ordre à confirmer au banc en écrivant GPIOD en sortie).
 * Lead-off : les bits LOFF_STATP/N ne valent que si LOFF_SENSP/N les active (fiche) ; à fixer à l'ouverture.
 * Lecture : le pilote lit le TIMER avant ET après la lecture SPI ; s'ils diffèrent, il jette la lecture (le saut
 * du TIMER la comptera comme DRDY manquée), jamais une donnée N+1 étiquetée N. */

/* Ouvre la session et écrit la trame d'OUVERTURE. codes[i] = index de la voie i dans la table des libellés
 * (trame.py LIBELLES). Retourne la longueur écrite (19 + n) ou un code d'erreur. */
int ubri_ouvrir(ubri_session *s, uint8_t *sortie, uint8_t n, const uint8_t *codes, uint32_t depart, uint16_t fs,
                uint8_t gain, uint32_t id_carte, uint16_t microprogramme, uint8_t bit_sync);

/* Un échantillon servi. timer = valeur du TIMER matériel (nombre de DRDY depuis l'ouverture, ce DRDY compris,
 * >= 1) ; ads = 27 octets lus en SPI ; etat_carte = octet libre (batterie). Écrit une trame v3 (18 + 3n octets).
 * Le compteur de la trame vaut depart + timer - 1 : une DRDY non servie fait sauter le compteur (cahier §3). */
int ubri_echantillon(ubri_session *s, uint8_t *sortie, uint32_t timer, const uint8_t ads[UBRI_ADS_OCTETS],
                     uint8_t etat_carte);

/* La radio n'a pas pu prendre `n` trames (tampon plein) : elles sont comptées, jamais oubliées. */
void ubri_abandon(ubri_session *s, uint32_t n);

/* À appeler AVANT la lecture SPI avec le TIMER lu : 1 si ce TIMER est nouveau (strictement plus grand que le dernier
 * servi), sinon compte une incohérence du TIMER et rend 0, et le pilote ne lit pas. Une interruption en retard (jeton
 * de sémaphore pour une DRDY déjà servie) ne déclenche ainsi ni lecture inutile ni faux refus ou fausse lecture
 * jetée, qui casseraient l'invariant jetées + refus <= DRDY manquées (KE#300). Hors session : 0, rien compté. */
int ubri_timer_coherent(ubri_session *s, uint32_t timer);

/* Le pilote a jeté une lecture (TIMER différent avant et après la lecture SPI). Compté pour la clôture v2. */
void ubri_lecture_jetee(ubri_session *s);

/* La lecture SPI elle-même a échoué. Compté dans `refus` (les refus de ubri_echantillon y sont comptés seuls). */
void ubri_refus_lecture(ubri_session *s);

/* Ferme la session et écrit la CLÔTURE v2 (0x83, 34 octets) : dernier émis, DRDY manquées, abandons, fronts
 * incertains, lectures jetées, refus, incohérences du TIMER. timer_final = valeur du TIMER à la fermeture : les DRDY après le dernier
 * échantillon servi sont comptées comme manquées, et « dernier émis » = dernière valeur du TIMER (cahier §6).
 * timer_final = 0 est admis (session fermée avant la première DRDY : dernier émis = départ - 1, 0 perte). */
int ubri_cloturer(ubri_session *s, uint8_t *sortie, uint32_t timer_final);

#endif
