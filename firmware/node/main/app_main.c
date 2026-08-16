#include "esp_err.h"
#include "esp_log.h"

#include "wcsi_protocol.h"

static const char *TAG = "wcsi_node";

void app_main(void)
{
    ESP_ERROR_CHECK(wcsi_protocol_self_test());
    ESP_LOGI(TAG, "WCSI protocol self-test PASS");
}
