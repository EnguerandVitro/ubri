/* Pilote minimal de l'ADS1299 pour l'application UBRI (SPI + broches de contrôle). Valeurs de registres tirées de
 * la fiche TI SBAS499C : « fiche, à vérifier au banc » tant qu'elles n'ont pas été relues sur la vraie carte. */
#ifndef UBRI_ADS1299_H
#define UBRI_ADS1299_H

#include <stdint.h>

#include "ubri_trame.h"

/* Réinitialise, vérifie l'identifiant (registre ID), écrit la configuration (250 SPS, gain 24, GPIO en entrée,
 * référence SRB1 commune, tampon BIAS) et la relit. Ne lance PAS les conversions. 0, ou un code négatif nommé. */
int ads1299_configurer(void);

/* START haut puis RDATAC : les DRDY commencent. À appeler juste après la remise à zéro du TIMER. */
int ads1299_lancer(void);

/* Arrête les conversions (SDATAC, START bas). */
void ads1299_arreter(void);

/* Lit les 27 octets d'un échantillon (mode RDATAC : aucune commande, 216 coups d'horloge après DRDY bas). */
int ads1299_lire(uint8_t ads[UBRI_ADS_OCTETS]);

#endif
