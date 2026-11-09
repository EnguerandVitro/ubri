/* Compteur MATÉRIEL des DRDY de l'ADS1299 (cahier carte-ubri-v0 §3) : la broche DRDY produit un événement GPIOTE IN,
 * relié par PPI à la tâche COUNT de TIMER2 en mode compteur. Le compte ne dépend d'aucune interruption : une DRDY
 * que le logiciel ne sert pas fait quand même avancer le TIMER, et le saut se voit dans le compteur de trame.
 * La même broche réveille le fil d'acquisition (interruption GPIOTE -> sémaphore à un jeton). */
#ifndef UBRI_COMPTEUR_DRDY_H
#define UBRI_COMPTEUR_DRDY_H

#include <stdint.h>

/* Configure GPIOTE (front descendant de DRDY), TIMER2 (compteur 32 bits) et la connexion PPI. 0 ou code négatif. */
int compteur_drdy_init(void);

/* Remet le TIMER à zéro (ouverture de session : TIMER = nombre de DRDY depuis l'ouverture). */
void compteur_drdy_zero(void);

/* Valeur actuelle du TIMER (capture CC0). */
uint32_t compteur_drdy_lire(void);

/* Attend la prochaine DRDY au plus timeout_ms : 0 = reçue, négatif sinon. */
int compteur_drdy_attendre(int32_t timeout_ms);

#endif
