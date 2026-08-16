#include "esp_err.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "nvs_flash.h"

#include "wcsi_node.h"
#include "wcsi_protocol.h"

static const char *TAG = "wcsi_node";

static void wcsi_wifi_event(void *arg, esp_event_base_t event_base,
                            int32_t event_id, void *event_data)
{
    (void)arg;
    (void)event_data;
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        wcsi_node_on_disconnected();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        wcsi_node_on_connected();
    }
}

void app_main(void)
{
#if CONFIG_WIFICSI_PROTOCOL_SELF_TEST
    ESP_ERROR_CHECK(wcsi_protocol_self_test());
    ESP_LOGI(TAG, "WCSI protocol self-test PASS");
    ESP_ERROR_CHECK(wcsi_node_queue_self_test());
    ESP_LOGI(TAG, "WCSI queue self-test PASS");
#endif

    esp_err_t nvs_error = nvs_flash_init();
    if (nvs_error == ESP_ERR_NVS_NO_FREE_PAGES ||
        nvs_error == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_error = nvs_flash_init();
    }
    ESP_ERROR_CHECK(nvs_error);
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED,
                                               wcsi_wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                               wcsi_wifi_event, NULL));
    ESP_ERROR_CHECK(wcsi_node_init());
}
