/* Application UBRI sur nRF52840 : ADS1299 (SPI) -> cœur ubri_trame (trames v3, ouverture, clôture) -> Bluetooth.
 *
 * Deux fils :
 * - ACQUISITION, seul à appeler les fonctions ubri_* (contrat de ubri_trame.h) : attend DRDY, lit le TIMER, lit les
 *   27 octets, relit le TIMER (différent => lecture jetée, le saut du TIMER la compte), encode, met en file ; file
 *   pleine => ubri_abandon(1). Il reverse dans ubri_abandon les pertes d'ÉCHANTILLONS de SA session postées par le
 *   fil BLE (KE#291). 2 s sans DRDY => session close (une ADS1299 muette n'a jamais l'air d'une session calme).
 * - BLE : vide la file, regroupe dans l'ordre des trames ENTIÈRES d'une même session, notifie ; une perte est postée
 *   avec sa session ; jamais d'accès à la session ubri.
 * Service GATT UBRI : COMMANDE (écriture : 0x01 ouvrir, 0x02 fermer), FLUX (notifications : trames, décodées par
 * products/ubri/connect/ubri_connect/trame.py).
 *
 * SÉCURITÉ (l'EEG est une donnée de santé) : LE Secure Connections seulement, niveau L4 exigé (permissions AUTHEN +
 * relecture du niveau avant chaque envoi). Clé ALÉATOIRE à chaque appairage, affichée sur la console uniquement
 * pendant la FENÊTRE D'APPAIRAGE (bouton 1 maintenu au démarrage, 60 s) ; hors fenêtre, tout appairage est refusé et
 * seuls les hôtes déjà liés (liaison mémorisée en flash) se reconnectent ; ouvrir la fenêtre EFFACE toutes les
 * liaisons. Une connexion qui n'atteint pas L4 en 30 s est coupée (place unique). La console passe par l'USB du PC : la
 * fenêtre d'appairage se fait SANS électrode posée (KE#285).
 * Compilé à blanc le 2026-11-09 ; JAMAIS exécuté sur une carte : tout comportement est à valider au banc. */
#include <errno.h>
#include <string.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>
#include <zephyr/spinlock.h>
#include <zephyr/sys/atomic.h>

#include "ads1299.h"
#include "compteur_drdy.h"
#include "ubri_trame.h"

LOG_MODULE_REGISTER(ubri, LOG_LEVEL_INF);

#define FS_HZ 250
#define GAIN 24
#define BIT_SYNC 0 /* GPIO1 de l'ADS1299 (ordre des bits à confirmer au banc, ubri_trame.h) */
#define ID_CARTE 0x0B1u
#define VERSION_MICROPROGRAMME 1u
#define FILE_PROFONDEUR 64          /* 64 trames ≈ 256 ms à 250 Hz */
#define SILENCE_MAX_MS 2000         /* sans DRDY pendant 2 s : ADS1299 muette, session close */
#define FENETRE_APPAIRAGE_MS 60000  /* bouton 1 au démarrage : 60 s pendant lesquelles un appairage est accepté */
/* Une connexion qui n'atteint pas L4 en 30 s est coupée : sinon un appareil inconnu garde la SEULE place
 * (CONFIG_BT_MAX_CONN = 1) et l'hôte lié ne peut plus se connecter. 30 s laisse lire la clé et la saisir ; durée
 * réelle d'un appairage à mesurer au banc (essai 6). */
#define DELAI_AUTH_MS 30000

typedef struct {
    uint32_t gen;        /* session émettrice : une perte n'est imputée qu'à SA session (KE#291) */
    uint8_t echantillon; /* 1 = trame v3 ; ouverture et clôture perdues ne sont PAS des échantillons abandonnés */
    uint8_t len;
    uint8_t data[UBRI_TRAME_V3_MAX];
} trame_t;

K_MSGQ_DEFINE(file_trames, sizeof(trame_t), FILE_PROFONDEUR, 4);
static atomic_t gen_session;  /* génération de la session en cours (incrémentée à chaque ouverture) */
static atomic_t pertes_ble;   /* échantillons de la session en cours perdus côté radio, postés par le fil BLE */
static atomic_t commande;     /* 0 rien, 1 ouvrir, 2 fermer : écrit par BLE, lu par l'acquisition */
static atomic_t seq_commande; /* +1 à CHAQUE commande (et à la déconnexion) : la commande est un ÉVÉNEMENT, pas un niveau.
                               * Sans lui, « fermer » puis « ouvrir » entre deux échantillons laissait la session courir
                               * sans nouvelle ouverture. */
static K_SEM_DEFINE(sem_commande, 0, 1);
static atomic_t flux_abonne;
static atomic_t fenetre_ouverte;

static struct k_spinlock verrou_conn; /* connexion_active n'est lue que sous verrou, et toujours avec une référence */
static struct bt_conn *connexion_active;

/* UUID propres à UBRI (128 bits, tirés une fois). */
#define UUID_SERVICE BT_UUID_128_ENCODE(0x6a1b0000, 0x5b3c, 0x4e0f, 0x9d2a, 0x7c1e0a5f3b21)
#define UUID_COMMANDE BT_UUID_128_ENCODE(0x6a1b0001, 0x5b3c, 0x4e0f, 0x9d2a, 0x7c1e0a5f3b21)
#define UUID_FLUX BT_UUID_128_ENCODE(0x6a1b0002, 0x5b3c, 0x4e0f, 0x9d2a, 0x7c1e0a5f3b21)
static const struct bt_uuid_128 uuid_service = BT_UUID_INIT_128(UUID_SERVICE);
static const struct bt_uuid_128 uuid_commande = BT_UUID_INIT_128(UUID_COMMANDE);
static const struct bt_uuid_128 uuid_flux = BT_UUID_INIT_128(UUID_FLUX);

static ssize_t ecrire_commande(struct bt_conn *conn, const struct bt_gatt_attr *attr, const void *buf, uint16_t len,
                               uint16_t offset, uint8_t flags)
{
    ARG_UNUSED(conn); ARG_UNUSED(attr); ARG_UNUSED(flags);
    if (offset != 0 || len != 1) return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
    uint8_t c = ((const uint8_t *)buf)[0];
    if (c != 1 && c != 2) return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);
    atomic_set(&commande, c);
    atomic_inc(&seq_commande);
    k_sem_give(&sem_commande);
    return len;
}

static void abonnement_flux(const struct bt_gatt_attr *attr, uint16_t valeur)
{
    ARG_UNUSED(attr);
    atomic_set(&flux_abonne, valeur == BT_GATT_CCC_NOTIFY);
}

BT_GATT_SERVICE_DEFINE(service_ubri,
    BT_GATT_PRIMARY_SERVICE(&uuid_service),
    BT_GATT_CHARACTERISTIC(&uuid_commande.uuid, BT_GATT_CHRC_WRITE, BT_GATT_PERM_WRITE_AUTHEN, NULL, ecrire_commande,
                           NULL),
    BT_GATT_CHARACTERISTIC(&uuid_flux.uuid, BT_GATT_CHRC_NOTIFY, BT_GATT_PERM_NONE, NULL, NULL, NULL),
    BT_GATT_CCC(abonnement_flux, BT_GATT_PERM_READ_AUTHEN | BT_GATT_PERM_WRITE_AUTHEN));
/* attrs : [0] service, [1][2] COMMANDE, [3][4] FLUX (déclaration, valeur), [5] CCC */
#define ATTR_FLUX_VALEUR (&service_ubri.attrs[4])

static const struct bt_data annonce[] = {
    BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
    BT_DATA_BYTES(BT_DATA_UUID128_ALL, UUID_SERVICE),
};
static const struct bt_data reponse[] = {
    BT_DATA(BT_DATA_NAME_COMPLETE, CONFIG_BT_DEVICE_NAME, sizeof(CONFIG_BT_DEVICE_NAME) - 1),
};

/* Zephyr >= 4 : une annonce connectable ne sert qu'UNE fois ; on la relance à chaque connexion recyclée (KE#292). */
static void relancer_annonce(struct k_work *w)
{
    ARG_UNUSED(w);
    int e = bt_le_adv_start(BT_LE_ADV_CONN_FAST_1, annonce, ARRAY_SIZE(annonce), reponse, ARRAY_SIZE(reponse));
    if (e != 0 && e != -EALREADY) LOG_ERR("annonce BLE non relancée (%d)", e);
}
static K_WORK_DEFINE(travail_annonce, relancer_annonce);

/* Garde d'authentification : relancée à CHAQUE connexion (k_work_reschedule), donc le délai d'une connexion
 * précédente ne coupe jamais la suivante avant ses 30 s ; au déclenchement, ne coupe que si le niveau est < L4. */
static void verifier_authentification(struct k_work *w)
{
    ARG_UNUSED(w);
    k_spinlock_key_t k = k_spin_lock(&verrou_conn);
    struct bt_conn *conn = connexion_active ? bt_conn_ref(connexion_active) : NULL;
    k_spin_unlock(&verrou_conn, k);
    if (conn == NULL) return;
    if (bt_conn_get_security(conn) < BT_SECURITY_L4) {
        LOG_ERR("connexion non authentifiée après %d s : déconnexion (place libérée)", DELAI_AUTH_MS / 1000);
        bt_conn_disconnect(conn, BT_HCI_ERR_AUTH_FAIL);
    }
    bt_conn_unref(conn);
}
static K_WORK_DELAYABLE_DEFINE(travail_auth, verifier_authentification);

static struct bt_gatt_exchange_params echange_mtu;
static void mtu_echangee(struct bt_conn *conn, uint8_t err, struct bt_gatt_exchange_params *p)
{
    ARG_UNUSED(p);
    if (err) LOG_ERR("échange de MTU refusé (%u) : aucune trame (27 à 42 octets) ne tiendra en 20", err);
    else LOG_INF("MTU %u", bt_gatt_get_mtu(conn));
}

static void connecte(struct bt_conn *conn, uint8_t err)
{
    if (err) return;
    k_spinlock_key_t k = k_spin_lock(&verrou_conn);
    connexion_active = bt_conn_ref(conn);
    k_spin_unlock(&verrou_conn, k);
    (void)k_work_reschedule(&travail_auth, K_MSEC(DELAI_AUTH_MS));
    /* L4 = LE Secure Connections + authentification : sans elle, ni commande ni abonnement (permissions AUTHEN) */
    int r = bt_conn_set_security(conn, BT_SECURITY_L4);
    if (r != 0) {
        LOG_ERR("sécurité L4 refusée (%d) : déconnexion", r);
        bt_conn_disconnect(conn, BT_HCI_ERR_AUTH_FAIL);
    }
}

static void deconnecte(struct bt_conn *conn, uint8_t raison)
{
    ARG_UNUSED(raison);
    struct bt_conn *ancienne = NULL;
    k_spinlock_key_t k = k_spin_lock(&verrou_conn);
    if (connexion_active == conn) {
        ancienne = connexion_active;
        connexion_active = NULL;
    }
    k_spin_unlock(&verrou_conn, k);
    if (ancienne) bt_conn_unref(ancienne);
    (void)k_work_cancel_delayable(&travail_auth);
    atomic_set(&flux_abonne, 0);
    atomic_inc(&seq_commande);
    atomic_set(&commande, 2); /* plus personne pour recevoir : la session se ferme (clôture perdue : l'hôte voit une
                               * session non bornée, perdus_total = None, jamais 0) */
    k_sem_give(&sem_commande);
}

static void securite_changee(struct bt_conn *conn, bt_security_t niveau, enum bt_security_err err)
{
    if (err != BT_SECURITY_ERR_SUCCESS || niveau < BT_SECURITY_L4) {
        LOG_ERR("appairage échoué ou niveau %d < L4 (erreur %d) : déconnexion", niveau, err);
        bt_conn_disconnect(conn, BT_HCI_ERR_AUTH_FAIL);
        return;
    }
    echange_mtu.func = mtu_echangee; /* la carte demande elle-même la MTU de 247 (sinon 20 octets utiles) */
    /* -EALREADY : déjà échangée sur CETTE connexion (niveau de sécurité rechangé, par ex. rafraîchissement de clé) ;
     * la pile n'autorise qu'un échange par connexion (gatt.c v4.4.2), ce n'est pas une erreur. */
    int e = bt_gatt_exchange_mtu(conn, &echange_mtu);
    if (e == -EALREADY) LOG_INF("MTU déjà échangée : %u", bt_gatt_get_mtu(conn));
    else if (e != 0) LOG_ERR("échange de MTU non lancé (%d)", e);
}

static void recyclee(void)
{
    k_work_submit(&travail_annonce);
}

BT_CONN_CB_DEFINE(rappels_connexion) = {
    .connected = connecte, .disconnected = deconnecte, .security_changed = securite_changee, .recycled = recyclee};

/* Appairage : clé ALÉATOIRE tirée par la pile à chaque appairage ; la carte « l'affiche » sur la console, uniquement
 * pendant la fenêtre d'appairage (sans électrode posée : la console passe par l'USB du PC). */
static void cle_affichee(struct bt_conn *conn, unsigned int cle)
{
    ARG_UNUSED(conn);
    if (atomic_get(&fenetre_ouverte)) printk("UBRI : clé d'appairage %06u\n", cle);
}

static enum bt_security_err appairage_accepte(struct bt_conn *conn, const struct bt_conn_pairing_feat *const feat)
{
    ARG_UNUSED(conn); ARG_UNUSED(feat);
    return atomic_get(&fenetre_ouverte) ? BT_SECURITY_ERR_SUCCESS : BT_SECURITY_ERR_PAIR_NOT_ALLOWED;
}

static void appairage_annule(struct bt_conn *conn)
{
    bt_conn_disconnect(conn, BT_HCI_ERR_AUTH_FAIL);
}

static struct bt_conn_auth_cb rappels_auth = {
    .passkey_display = cle_affichee, .pairing_accept = appairage_accepte, .cancel = appairage_annule};

static void fermer_fenetre(struct k_work *w)
{
    ARG_UNUSED(w);
    atomic_clear(&fenetre_ouverte);
    LOG_INF("fenêtre d'appairage close : seuls les hôtes déjà liés se reconnectent");
}
static K_WORK_DELAYABLE_DEFINE(travail_fenetre, fermer_fenetre);

static int en_file(const uint8_t *data, int len, bool bloquant, bool echantillon)
{
    trame_t t = {.gen = (uint32_t)atomic_get(&gen_session), .echantillon = echantillon, .len = (uint8_t)len};
    memcpy(t.data, data, (size_t)len);
    return k_msgq_put(&file_trames, &t, bloquant ? K_MSEC(200) : K_NO_WAIT);
}

static void reverser_pertes_ble(ubri_session *s)
{
    atomic_val_t n = atomic_clear(&pertes_ble);
    if (n > 0) ubri_abandon(s, (uint32_t)n);
}

static void fil_acquisition(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    static const uint8_t codes[8] = {0, 1, 2, 3, 4, 5, 6, 7}; /* Fz FCz Cz CPz Pz C3 C4 EOG (trame.py LIBELLES) */
    ubri_session s;
    memset(&s, 0, sizeof s);
    uint8_t sortie[UBRI_TRAME_V3_MAX];
    uint8_t ads[UBRI_ADS_OCTETS];
    for (;;) {
        k_sem_take(&sem_commande, K_FOREVER);
        /* seq lu AVANT commande : une commande arrivée après cette lecture ferme la session via seq, aucune n'est
         * absorbée ; un « ouvrir » rouvre ensuite une session NEUVE */
        atomic_val_t seq_ouverture = atomic_get(&seq_commande);
        if (atomic_get(&commande) != 1) continue;
        atomic_inc(&gen_session);
        (void)atomic_clear(&pertes_ble); /* pertes d'une session antérieure : jamais imputées à celle-ci (KE#291) */
        int r = ads1299_configurer();     /* RESET, ID, registres, relecture : AVANT la remise à zéro du TIMER */
        compteur_drdy_zero();             /* juste avant START : aucune DRDY parasite de la configuration comptée */
        int n = ubri_ouvrir(&s, sortie, 8, codes, 0, FS_HZ, GAIN, ID_CARTE, VERSION_MICROPROGRAMME, BIT_SYNC);
        if (n < 0 || en_file(sortie, n, true, false) != 0) {
            LOG_ERR("ouverture impossible (%d)", n);
            memset(&s, 0, sizeof s); /* ubri_ouvrir a pu réussir : sinon UBRI_ERR_SESSION jusqu'au redémarrage */
            continue;
        }
        if (r == 0 && atomic_get(&seq_commande) == seq_ouverture) r = ads1299_lancer(); /* START puis RDATAC ; pas de
                                                                         * START inutile si une commande est arrivée */
        if (r != 0) LOG_ERR("ADS1299 non démarrée (%d) : la session sera close sans échantillon", r);
        uint32_t silences = 0;
        while (r == 0 && atomic_get(&commande) == 1 && atomic_get(&seq_commande) == seq_ouverture) {
            if (compteur_drdy_attendre(100) != 0) {
                if (++silences >= SILENCE_MAX_MS / 100) {
                    LOG_ERR("aucune DRDY depuis %d ms : session close", SILENCE_MAX_MS);
                    break;
                }
                continue;
            }
            silences = 0;
            uint32_t t1 = compteur_drdy_lire();
            /* interruption en retard (jeton pour une DRDY déjà servie) : ni lecture, ni faux refus (KE#300) */
            if (!ubri_timer_coherent(&s, t1)) continue;
            if (ads1299_lire(ads) != 0) { ubri_refus_lecture(&s); continue; }
            uint32_t t2 = compteur_drdy_lire();
            if (t1 != t2) { ubri_lecture_jetee(&s); continue; } /* DRDY pendant la lecture : jamais N+1 étiquetée N */
            reverser_pertes_ble(&s);
            n = ubri_echantillon(&s, sortie, t1, ads, 0);
            if (n < 0) continue; /* mot d'état corrompu : s.refus ; TIMER incohérent : s.incoherences_timer (KE#300) */
            if (en_file(sortie, n, false, true) != 0) ubri_abandon(&s, 1);
        }
        ads1299_arreter();
        reverser_pertes_ble(&s);
        n = ubri_cloturer(&s, sortie, compteur_drdy_lire());
        if (n < 0 || en_file(sortie, n, true, false) != 0) LOG_ERR("clôture non émise (%d)", n);
        LOG_INF("session close : %u DRDY manquées, %u abandons, %u fronts incertains, %u lectures jetées, %u refus, "
                "%u incohérences du TIMER", s.drdy_manquees, s.abandonnees, s.fronts_incertains, s.lectures_jetees,
                s.refus, s.incoherences_timer); /* tous transmis (clôture v2) */
        memset(&s, 0, sizeof s);
    }
}

static void fil_ble(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    static uint8_t paquet[244]; /* ATT MTU 247 - 3 octets d'en-tête */
    static trame_t en_attente;  /* trame sortie de la file mais pas encore placée : garde l'ORDRE des trames */
    bool attente = false;
    for (;;) {
        if (!attente) {
            k_msgq_get(&file_trames, &en_attente, K_FOREVER);
            attente = true;
        }
        k_spinlock_key_t k = k_spin_lock(&verrou_conn);
        struct bt_conn *conn = connexion_active ? bt_conn_ref(connexion_active) : NULL;
        k_spin_unlock(&verrou_conn, k);
        size_t utile = conn ? MIN(sizeof paquet, (size_t)bt_gatt_get_mtu(conn) - 3u) : 0;
        size_t remplissage = 0;
        uint32_t nb = 0, nb_ech = 0, gen_paquet = en_attente.gen;
        /* trames ENTIÈRES d'une même session, dans l'ordre ; la première qui ne rentre plus reste en attente */
        while (attente && en_attente.gen == gen_paquet && remplissage + en_attente.len <= utile) {
            memcpy(paquet + remplissage, en_attente.data, en_attente.len);
            remplissage += en_attente.len;
            nb++;
            nb_ech += en_attente.echantillon;
            attente = (k_msgq_get(&file_trames, &en_attente, K_NO_WAIT) == 0);
        }
        /* la session du paquet est relue JUSTE avant chaque imputation (KE#291) : bt_gatt_notify peut attendre un tampon
         * ATT en K_FOREVER (att.c v4.4.2), la comparer avant l'envoi élargirait la fenêtre de course à cette attente */
        if (nb == 0) { /* pas de connexion, ou MTU trop petite pour une seule trame : perdue */
            if (en_attente.echantillon && gen_paquet == (uint32_t)atomic_get(&gen_session)) atomic_inc(&pertes_ble);
            attente = false;
        } else if (!atomic_get(&flux_abonne) || bt_conn_get_security(conn) < BT_SECURITY_L4 ||
                   bt_gatt_notify(conn, ATTR_FLUX_VALEUR, paquet, (uint16_t)remplissage) != 0) {
            /* deux barrières indépendantes : abonnement (CCC en AUTHEN) ET niveau de la liaison relu ici */
            if (gen_paquet == (uint32_t)atomic_get(&gen_session)) atomic_add(&pertes_ble, (atomic_val_t)nb_ech);
        }
        if (conn) bt_conn_unref(conn);
    }
}

K_THREAD_DEFINE(id_acquisition, 2048, fil_acquisition, NULL, NULL, NULL, 2, 0, 0);
K_THREAD_DEFINE(id_ble, 2048, fil_ble, NULL, NULL, NULL, 5, 0, 0);

static const struct gpio_dt_spec bouton = GPIO_DT_SPEC_GET(DT_ALIAS(sw0), gpios);

static void compter_liaison(const struct bt_bond_info *info, void *n)
{
    ARG_UNUSED(info);
    (*(int *)n)++;
}

int main(void)
{
    int err = compteur_drdy_init();
    if (err != 0) {
        LOG_ERR("compteur DRDY : %d (aucune session possible)", err);
        return err;
    }
    if (gpio_is_ready_dt(&bouton) && gpio_pin_configure_dt(&bouton, GPIO_INPUT) == 0 && gpio_pin_get_dt(&bouton) == 1) {
        atomic_set(&fenetre_ouverte, 1);
        k_work_schedule(&travail_fenetre, K_MSEC(FENETRE_APPAIRAGE_MS));
        printk("UBRI : fenêtre d'appairage ouverte %d s (SANS électrode posée : console sur l'USB du PC)\n",
               FENETRE_APPAIRAGE_MS / 1000);
    }
    err = bt_conn_auth_cb_register(&rappels_auth);
    if (err != 0) {
        LOG_ERR("rappels d'authentification refusés (%d)", err);
        return err;
    }
    err = bt_enable(NULL);
    if (err != 0) {
        LOG_ERR("Bluetooth : %d", err);
        return err;
    }
    err = settings_load(); /* liaisons mémorisées : un hôte déjà lié se reconnecte hors fenêtre d'appairage */
    if (err != 0) {
        LOG_ERR("liaisons mémorisées non chargées (%d)", err);
        if (atomic_get(&fenetre_ouverte)) {
            /* effacement impossible à certifier : bt_unpair ne voit que les liaisons chargées en RAM (KE#294) */
            atomic_clear(&fenetre_ouverte);
            LOG_ERR("fenêtre d'appairage refermée : liaisons en flash inconnues");
        }
    }
    if (atomic_get(&fenetre_ouverte)) {
        /* Ouvrir la fenêtre EFFACE toutes les liaisons (APRÈS settings_load, sinon elles reviendraient de la flash) :
         * c'est le seul moyen de révoquer un hôte perdu, et sans cela, BT_MAX_PAIRED = 2 atteint, tout nouvel
         * appairage échouerait. Geste physique sur la carte = remise à zéro de la confiance.
         * Zephyr v4.4.2 : bt_unpair(ANY) rend 0 sans condition (hci_core.c l. 2182-2197) : on RECOMPTE (KE#294).
         * L'effacement en flash lui-même n'est pas vérifiable ici : essai 6 du banc (redémarrage SANS bouton). */
        (void)bt_unpair(BT_ID_DEFAULT, BT_ADDR_LE_ANY);
        int restantes = 0;
        bt_foreach_bond(BT_ID_DEFAULT, compter_liaison, &restantes);
        if (restantes != 0) {
            LOG_ERR("%d liaison(s) non effacée(s) : fenêtre d'appairage refermée", restantes);
            atomic_clear(&fenetre_ouverte);
        } else {
            printk("UBRI : liaisons précédentes effacées, appairer à nouveau chaque hôte\n");
        }
    }
    relancer_annonce(NULL);
    return 0;
}
