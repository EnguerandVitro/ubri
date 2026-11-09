/* Compteur matériel des DRDY : GPIOTE IN -> PPI -> TIMER2 COUNT. Montage repris de l'exemple Zephyr
 * samples/subsys/usb/uac2_explicit_feedback/src/feedback_nrf.c (nrfx 4.2). Voir compteur_drdy.h. */
#include "compteur_drdy.h"

#include <gpiote_nrfx.h>
#include <helpers/nrfx_gppi.h>
#include <nrfx_gpiote.h>
#include <nrfx_timer.h>
#include <zephyr/devicetree.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(compteur_drdy, LOG_LEVEL_INF);

#define BROCHE_DRDY NRF_DT_GPIOS_TO_PSEL(DT_NODELABEL(ads1299), drdy_gpios)

static nrfx_timer_t minuteur = NRFX_TIMER_INSTANCE(NRF_TIMER_INST_GET(2)); /* TIMER0 est pris par la pile BLE */
static nrfx_gpiote_t *gpiote = &GPIOTE_NRFX_INST_BY_NODE(DT_NODELABEL(gpiote0));
static K_SEM_DEFINE(sem_drdy, 0, 1);

static void sur_drdy(nrfx_gpiote_pin_t broche, nrfx_gpiote_trigger_t declencheur, void *ctx)
{
    ARG_UNUSED(broche); ARG_UNUSED(declencheur); ARG_UNUSED(ctx);
    k_sem_give(&sem_drdy); /* un seul jeton : une DRDY non servie à temps ne s'accumule pas, le TIMER la compte */
}

int compteur_drdy_init(void)
{
    uint8_t canal;
    int err = nrfx_gpiote_channel_alloc(gpiote, &canal);
    if (err != 0) {
        LOG_ERR("canal GPIOTE indisponible (%d)", err);
        return err;
    }
    nrfx_gpiote_trigger_config_t declenche = {
        .trigger = NRFX_GPIOTE_TRIGGER_HITOLO, /* DRDY active basse : un front descendant par échantillon */
        .p_in_channel = &canal,
    };
    nrfx_gpiote_handler_config_t gestion = {.handler = sur_drdy, .p_context = NULL};
    nrf_gpio_pin_pull_t tirage = NRF_GPIO_PIN_NOPULL; /* DRDY est une sortie poussée de l'ADS1299 */
    nrfx_gpiote_input_pin_config_t config = {
        .p_pull_config = &tirage,
        .p_trigger_config = &declenche,
        .p_handler_config = &gestion,
    };
    err = nrfx_gpiote_input_configure(gpiote, BROCHE_DRDY, &config);
    if (err != 0) {
        LOG_ERR("configuration GPIOTE de DRDY refusée (%d)", err);
        return err;
    }
    const nrfx_timer_config_t cfg = {
        .frequency = NRFX_MHZ_TO_HZ(1UL), /* sans effet en mode compteur, exigé par l'API */
        .mode = NRF_TIMER_MODE_COUNTER,
        .bit_width = NRF_TIMER_BIT_WIDTH_32,
        .interrupt_priority = NRFX_TIMER_DEFAULT_CONFIG_IRQ_PRIORITY,
        .p_context = NULL,
    };
    err = nrfx_timer_init(&minuteur, &cfg, NULL);
    if (err != 0) {
        LOG_ERR("TIMER2 indisponible (%d)", err);
        return err;
    }
    uint32_t eep = nrfx_gpiote_in_event_address_get(gpiote, BROCHE_DRDY);
    uint32_t tep = nrfx_timer_task_address_get(&minuteur, NRF_TIMER_TASK_COUNT);
    nrfx_gppi_handle_t connexion;
    err = nrfx_gppi_conn_alloc(eep, tep, &connexion);
    if (err < 0) {
        LOG_ERR("connexion PPI DRDY -> TIMER2 refusée (%d)", err);
        return err;
    }
    nrfx_gppi_conn_enable(connexion);
    nrfx_timer_enable(&minuteur);
    nrfx_gpiote_trigger_enable(gpiote, BROCHE_DRDY, true); /* true : interruption en plus de l'événement PPI */
    return 0;
}

void compteur_drdy_zero(void)
{
    nrfx_timer_clear(&minuteur);
    k_sem_reset(&sem_drdy);
}

uint32_t compteur_drdy_lire(void)
{
    return nrfx_timer_capture(&minuteur, NRF_TIMER_CC_CHANNEL0);
}

int compteur_drdy_attendre(int32_t timeout_ms)
{
    return k_sem_take(&sem_drdy, K_MSEC(timeout_ms));
}
