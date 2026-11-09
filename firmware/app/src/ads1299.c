/* Pilote minimal de l'ADS1299. Voir ads1299.h. */
#include "ads1299.h"

#include <errno.h>
#include <string.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(ads1299, LOG_LEVEL_INF);

#define ADS_NOEUD DT_NODELABEL(ads1299)
/* fiche : CPOL = 0, CPHA = 1 (données lues sur le front descendant de SCLK), MSB d'abord, mots de 8 bits */
static const struct spi_dt_spec bus = SPI_DT_SPEC_GET(ADS_NOEUD, SPI_WORD_SET(8) | SPI_TRANSFER_MSB | SPI_MODE_CPHA);
static const struct gpio_dt_spec start = GPIO_DT_SPEC_GET(ADS_NOEUD, start_gpios);
static const struct gpio_dt_spec reset = GPIO_DT_SPEC_GET(ADS_NOEUD, reset_gpios);

/* Commandes et registres (fiche SBAS499C, à vérifier au banc). */
enum {
    CMD_RESET = 0x06, CMD_RDATAC = 0x10, CMD_SDATAC = 0x11, CMD_RREG = 0x20, CMD_WREG = 0x40,
    REG_ID = 0x00, REG_CONFIG1 = 0x01, REG_GPIO = 0x14, REG_MISC1 = 0x15,
};
#define ID_ADS1299_8VOIES 0x3Eu /* fiche : 0x3E pour l'ADS1299 8 voies (à vérifier au banc) */

/* CONFIG1..CH8SET (adresses 0x01..0x0C), écrites d'un bloc. Fiche, à vérifier au banc :
 * CONFIG1 0x96 : 250 SPS, horloge interne non sortie ; CONFIG2 0xC0 : signaux de test coupés ;
 * CONFIG3 0xEC : tampon de référence interne + amplificateur BIAS alimenté, BIASREF interne. Sans BIAS_SENSP/N
 *   (registres 0x0D/0x0E à 0), il ne fait que piloter BIASREF : PAS de contre-réaction « jambe droite » (à régler au banc) ;
 * LOFF 0x00 : lead-off DC au repos (bits LOFF_STAT invalides tant que LOFF_SENSP/N = 0, voir ubri_trame.h) ;
 * CH1SET..CH8SET 0x60 : gain 24, entrée électrode normale. */
static const uint8_t CONFIG_BLOC[12] = {0x96, 0xC0, 0xEC, 0x00, 0x60, 0x60, 0x60, 0x60, 0x60, 0x60, 0x60, 0x60};

static int ecrire(const uint8_t *tx, size_t n)
{
    const struct spi_buf b = {.buf = (void *)tx, .len = n};
    const struct spi_buf_set s = {.buffers = &b, .count = 1};
    return spi_write_dt(&bus, &s);
}

static int commande(uint8_t c)
{
    int r = ecrire(&c, 1);
    k_busy_wait(4); /* fiche : au moins 4 tCLK (≈ 2 µs à 2,048 MHz) entre deux commandes */
    return r;
}

static int lire_registre(uint8_t adr, uint8_t *val)
{
    uint8_t tx[3] = {(uint8_t)(CMD_RREG | adr), 0x00, 0x00}, rx[3] = {0};
    const struct spi_buf bt = {.buf = tx, .len = 3}, br = {.buf = rx, .len = 3};
    const struct spi_buf_set st = {.buffers = &bt, .count = 1}, sr = {.buffers = &br, .count = 1};
    int r = spi_transceive_dt(&bus, &st, &sr);
    *val = rx[2];
    return r;
}

static int ecrire_registres(uint8_t adr, const uint8_t *val, uint8_t n)
{
    uint8_t tx[2 + 12];
    if (n == 0 || n > 12) return -EINVAL;
    tx[0] = (uint8_t)(CMD_WREG | adr);
    tx[1] = (uint8_t)(n - 1u);
    memcpy(&tx[2], val, n);
    return ecrire(tx, 2u + n);
}

int ads1299_configurer(void)
{
    int r;
    if (!spi_is_ready_dt(&bus) || !gpio_is_ready_dt(&start) || !gpio_is_ready_dt(&reset)) {
        LOG_ERR("SPI ou GPIO de l'ADS1299 non prêts");
        return -ENODEV;
    }
    gpio_pin_configure_dt(&start, GPIO_OUTPUT_INACTIVE);
    gpio_pin_configure_dt(&reset, GPIO_OUTPUT_ACTIVE);
    k_busy_wait(10);
    gpio_pin_set_dt(&reset, 0);
    k_msleep(10); /* fiche : attendre 18 tCLK après RESET ; large marge */
    if ((r = commande(CMD_SDATAC)) != 0) return r; /* sortir du mode continu avant d'écrire les registres */
    uint8_t id = 0;
    if ((r = lire_registre(REG_ID, &id)) != 0) return r;
    if (id != ID_ADS1299_8VOIES) {
        LOG_ERR("identifiant ADS1299 inattendu : 0x%02x (attendu 0x%02x)", id, ID_ADS1299_8VOIES);
        return -EIO;
    }
    const uint8_t gpio = 0x0F, misc1 = 0x20; /* 4 GPIO en entrée (synchro sur GPIO1) ; SRB1 sur toutes les entrées - */
    if ((r = ecrire_registres(REG_CONFIG1, CONFIG_BLOC, sizeof CONFIG_BLOC)) != 0) return r;
    if ((r = ecrire_registres(REG_GPIO, &gpio, 1)) != 0) return r;
    if ((r = ecrire_registres(REG_MISC1, &misc1, 1)) != 0) return r;
    uint8_t relu = 0;
    if ((r = lire_registre(REG_CONFIG1, &relu)) != 0) return r;
    if (relu != CONFIG_BLOC[0]) { /* relecture : une écriture SPI ratée ne passe pas pour une configuration */
        LOG_ERR("CONFIG1 relu 0x%02x, écrit 0x%02x", relu, CONFIG_BLOC[0]);
        return -EIO;
    }
    return 0;
}

int ads1299_lancer(void)
{
    gpio_pin_set_dt(&start, 1);
    return commande(CMD_RDATAC);
}

void ads1299_arreter(void)
{
    gpio_pin_set_dt(&start, 0);
    (void)commande(CMD_SDATAC);
}

int ads1299_lire(uint8_t ads[UBRI_ADS_OCTETS])
{
    static const uint8_t zeros[UBRI_ADS_OCTETS] = {0};
    const struct spi_buf bt = {.buf = (void *)zeros, .len = UBRI_ADS_OCTETS}, br = {.buf = ads, .len = UBRI_ADS_OCTETS};
    const struct spi_buf_set st = {.buffers = &bt, .count = 1}, sr = {.buffers = &br, .count = 1};
    return spi_transceive_dt(&bus, &st, &sr);
}
