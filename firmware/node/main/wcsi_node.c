#include "wcsi_node.h"

#include <errno.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"

#define WCSI_CONTROL_QUEUE_CAPACITY 16U
#define WCSI_ACK_CACHE_CAPACITY 16U
#define WCSI_CAPABILITIES 0x000fU
#define WCSI_FIRMWARE_MAJOR 1U
#define WCSI_FIRMWARE_MINOR 0U
#define WCSI_FIRMWARE_PATCH 0U
#define WCSI_STATE_UNKNOWN 2U
#define WCSI_STATE_REASON_PERIODIC 0U
#define WCSI_STATE_REASON_ACTIVE 1U
#define WCSI_STATE_REASON_INACTIVE 2U
#define WCSI_STATE_REASON_RECONNECT 3U
#define WCSI_STATE_REASON_RESET 4U
#define WCSI_FLAG_PRESENCE_READY (1U << 0)
#define WCSI_FLAG_PRESENCE_STATUS (1U << 1)
#define WCSI_FLAG_EVENT_TRIGGERED (1U << 2)

typedef struct __attribute__((packed)) {
    uint8_t source_mac[6];
    uint8_t destination_mac[6];
    int8_t rssi;
    int8_t noise_floor;
    uint8_t channel;
    uint8_t secondary_channel;
    uint32_t rx_timestamp;
    uint16_t signal_length;
    uint16_t csi_length;
    uint8_t rate;
    uint8_t signal_mode;
    uint8_t mcs;
    uint8_t bandwidth;
    uint16_t phy_flags;
    uint8_t antenna;
    uint8_t rx_state;
    uint8_t first_word_invalid;
    uint8_t reserved[3];
} wcsi_csi_metadata_t;

typedef struct {
    wcsi_csi_metadata_t metadata;
    uint8_t iq[WCSI_MAX_CSI_LENGTH];
} wcsi_csi_queue_item_t;

typedef struct {
    uint8_t reason;
    uint8_t stable_state;
    bool has_stable_override;
} wcsi_control_item_t;

typedef struct {
    bool valid;
    uint32_t boot_id;
    uint32_t correlation_id;
    size_t length;
    uint8_t datagram[WCSI_MAX_DATAGRAM];
} wcsi_ack_cache_entry_t;

_Static_assert(sizeof(wcsi_csi_metadata_t) == WCSI_NODE_CSI_METADATA_SIZE,
               "CSI queue metadata must match the 36-byte wire model");
_Static_assert(sizeof(((wcsi_csi_queue_item_t *)0)->iq) == WCSI_MAX_CSI_LENGTH,
               "CSI queue I/Q capacity must match the protocol maximum");

static const char *TAG = "wcsi_runtime";
static const uint32_t RECONNECT_DELAYS_MS[] = {1000, 2000, 4000, 8000, 15000};

static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static QueueHandle_t s_csi_queue;
static QueueHandle_t s_control_queue;
static SemaphoreHandle_t s_tx_mutex;
static SemaphoreHandle_t s_sensing_mutex;
static StaticQueue_t s_csi_queue_state;
static uint8_t *s_csi_queue_storage;
static int s_socket = -1;
static struct sockaddr_in s_server_address;
static esp_wifi_sensing_fsm_handle_t s_fsm;
static uint8_t s_node_id[6];
static uint8_t s_ap_bssid[6];
static uint8_t s_ap_channel;
static uint32_t s_boot_id;
static uint32_t s_sequence;
static uint32_t s_csi_accepted;
static uint32_t s_csi_sent;
static uint32_t s_queue_dropped;
static uint32_t s_udp_send_errors;
static uint32_t s_reconnect_attempt;
static bool s_connected;
static bool s_sensing_ready;
static bool s_hello_requested;
static TaskHandle_t s_reconnect_task;
static TaskHandle_t s_sensing_retry_task;
static wcsi_ack_cache_entry_t s_ack_cache[WCSI_ACK_CACHE_CAPACITY];
static size_t s_ack_cache_next;

#if CONFIG_WCSI_SENSING_INIT_FAULT_SELF_TEST
static bool s_sensing_failure_injected;
#endif

#if CONFIG_WCSI_TX_ORDER_FAULT_SELF_TEST
static bool s_tx_delay_injected;
#endif

static uint32_t locked_increment(uint32_t *counter)
{
    uint32_t value;
    portENTER_CRITICAL(&s_lock);
    value = ++(*counter);
    portEXIT_CRITICAL(&s_lock);
    return value;
}

static uint32_t locked_read(const uint32_t *counter)
{
    uint32_t value;
    portENTER_CRITICAL(&s_lock);
    value = *counter;
    portEXIT_CRITICAL(&s_lock);
    return value;
}

static bool node_is_connected(void)
{
    bool connected;
    portENTER_CRITICAL(&s_lock);
    connected = s_connected;
    portEXIT_CRITICAL(&s_lock);
    return connected;
}

static void snapshot_ap_identity(uint8_t bssid[6], uint8_t *channel)
{
    portENTER_CRITICAL(&s_lock);
    memcpy(bssid, s_ap_bssid, sizeof(s_ap_bssid));
    if (channel != NULL) {
        *channel = s_ap_channel;
    }
    portEXIT_CRITICAL(&s_lock);
}

static bool ap_identity_matches(const uint8_t bssid[6])
{
    bool matches;
    portENTER_CRITICAL(&s_lock);
    matches = s_connected && memcmp(bssid, s_ap_bssid, sizeof(s_ap_bssid)) == 0;
    portEXIT_CRITICAL(&s_lock);
    return matches;
}

static bool enqueue_csi(QueueHandle_t queue, const wcsi_csi_queue_item_t *item,
                        uint32_t *accepted, uint32_t *dropped)
{
    if (xQueueSend(queue, item, 0) == pdTRUE) {
        locked_increment(accepted);
        return true;
    }
    locked_increment(dropped);
    return false;
}

static wcsi_header_t next_header(wcsi_message_type_t message_type)
{
    wcsi_header_t header = {
        .boot_id = s_boot_id,
        .device_time_us = (uint64_t)esp_timer_get_time(),
        .message_type = message_type,
    };
    memcpy(header.node_id, s_node_id, sizeof(header.node_id));
    portENTER_CRITICAL(&s_lock);
    header.sequence = s_sequence++;
    portEXIT_CRITICAL(&s_lock);
    return header;
}

static bool send_datagram_unlocked(const uint8_t *datagram, size_t length,
                                   const struct sockaddr_in *destination)
{
    int sent = sendto(s_socket, datagram, length, 0,
                      (const struct sockaddr *)destination, sizeof(*destination));
    if (sent != (int)length) {
        locked_increment(&s_udp_send_errors);
        return false;
    }
    return true;
}

static bool send_cached_datagram_to(const uint8_t *datagram, size_t length,
                                    const struct sockaddr_in *destination)
{
    xSemaphoreTake(s_tx_mutex, portMAX_DELAY);
    bool sent = send_datagram_unlocked(datagram, length, destination);
    xSemaphoreGive(s_tx_mutex);
    return sent;
}

static bool send_hello(void)
{
    uint8_t datagram[WCSI_MAX_DATAGRAM];
    size_t length;
    wcsi_hello_t hello = {
        .chip_model = 1,
        .firmware_major = WCSI_FIRMWARE_MAJOR,
        .firmware_minor = WCSI_FIRMWARE_MINOR,
        .firmware_patch = WCSI_FIRMWARE_PATCH,
        .capabilities = WCSI_CAPABILITIES,
        .max_csi_length = WCSI_MAX_CSI_LENGTH,
        .queue_capacity = CONFIG_WCSI_QUEUE_CAPACITY,
        .heartbeat_interval_ms = CONFIG_WCSI_HEARTBEAT_PERIOD_MS,
        .state_interval_ms = CONFIG_WCSI_STATE_PERIOD_MS,
        .listen_port = CONFIG_WCSI_LOCAL_PORT,
    };
    snapshot_ap_identity(hello.ap_bssid, NULL);
    xSemaphoreTake(s_tx_mutex, portMAX_DELAY);
    wcsi_header_t header = next_header(WCSI_MESSAGE_HELLO);
    bool sent = wcsi_encode_hello(&header, &hello, datagram, sizeof(datagram), &length) ==
                    ESP_OK &&
                send_datagram_unlocked(datagram, length, &s_server_address);
    xSemaphoreGive(s_tx_mutex);
    return sent;
}

static void copy_public_config(wcsi_sensing_config_t *destination,
                               const esp_wifi_sensing_fsm_channel_config_t *source)
{
    destination->motion_sensitivity = source->sensitivity;
    destination->presence_sensitivity = source->presence_sensitivity;
    destination->active_jitter_min = source->active_jitter_min;
    destination->active_filter_ms = source->active_filter_ms;
}

static bool send_sensing(uint8_t reason, bool has_override, uint8_t stable_override)
{
    uint8_t datagram[WCSI_MAX_DATAGRAM];
    size_t length;
    wcsi_sensing_t sensing = {
        .stable_state = WCSI_STATE_UNKNOWN,
        .reason = reason,
    };
    snapshot_ap_identity(sensing.peer_mac, NULL);

    xSemaphoreTake(s_sensing_mutex, portMAX_DELAY);
    esp_wifi_sensing_fsm_handle_t fsm;
    bool sensing_ready;
    portENTER_CRITICAL(&s_lock);
    fsm = s_fsm;
    sensing_ready = s_sensing_ready;
    portEXIT_CRITICAL(&s_lock);
    if (fsm != NULL && sensing_ready) {
        esp_wifi_sensing_fsm_state_t state;
        esp_wifi_sensing_fsm_channel_diag_t diag = {0};
        esp_wifi_sensing_fsm_channel_config_t config = {0};
        esp_err_t state_error = esp_wifi_sensing_fsm_get_state(fsm, sensing.peer_mac, &state);
        esp_err_t diag_error = esp_wifi_sensing_fsm_get_channel_diag(fsm, sensing.peer_mac,
                                                                     &diag);
        esp_err_t config_error = esp_wifi_sensing_fsm_get_channel_config(
            fsm, sensing.peer_mac, &config);
        if (diag_error == ESP_OK) {
            sensing.process_state = (uint8_t)diag.state;
            sensing.init_stage = (uint8_t)diag.init_stage;
            sensing.jitter = diag.jitter_value;
            sensing.wander = diag.wander_value;
            sensing.smooth_scaled = diag.smooth_scaled;
            sensing.enter_level_scaled = diag.enter_level_scaled;
            sensing.exit_level_scaled = diag.exit_level_scaled;
            sensing.presence_wander_average = diag.presence_wander_average;
            sensing.presence_someone_threshold = diag.presence_someone_threshold;
            if (diag.presence_ready) {
                sensing.flags |= WCSI_FLAG_PRESENCE_READY;
            }
            if (diag.presence_someone_status) {
                sensing.flags |= WCSI_FLAG_PRESENCE_STATUS;
            }
            if (diag.init_stage == ESP_WIFI_SENSING_FSM_INIT_STAGE_STABLE &&
                state_error == ESP_OK) {
                if (state == ESP_WIFI_SENSING_FSM_STATE_INACTIVE) {
                    sensing.stable_state = 0;
                } else if (state == ESP_WIFI_SENSING_FSM_STATE_ACTIVE) {
                    sensing.stable_state = 1;
                }
            }
        }
        if (config_error == ESP_OK) {
            copy_public_config(&sensing.config, &config);
        }
    }
    xSemaphoreGive(s_sensing_mutex);
    if (has_override) {
        sensing.stable_state = stable_override;
        sensing.flags |= WCSI_FLAG_EVENT_TRIGGERED;
    }

    xSemaphoreTake(s_tx_mutex, portMAX_DELAY);
    wcsi_header_t header = next_header(WCSI_MESSAGE_SENSING_STATE);
    bool sent = wcsi_encode_sensing(&header, &sensing, datagram, sizeof(datagram), &length) ==
                    ESP_OK &&
                send_datagram_unlocked(datagram, length, &s_server_address);
    xSemaphoreGive(s_tx_mutex);
    return sent;
}

static bool send_heartbeat(void)
{
    uint8_t datagram[WCSI_MAX_DATAGRAM];
    size_t length;
    wcsi_heartbeat_t heartbeat = {
        .uptime_us = (uint64_t)esp_timer_get_time(),
        .free_heap = esp_get_free_heap_size(),
        .minimum_free_heap = esp_get_minimum_free_heap_size(),
        .csi_accepted = locked_read(&s_csi_accepted),
        .csi_sent = locked_read(&s_csi_sent),
        .queue_dropped = locked_read(&s_queue_dropped),
        .udp_send_errors = locked_read(&s_udp_send_errors),
    };
    xSemaphoreTake(s_tx_mutex, portMAX_DELAY);
    wcsi_header_t header = next_header(WCSI_MESSAGE_HEARTBEAT);
    bool sent = wcsi_encode_heartbeat(&header, &heartbeat, datagram, sizeof(datagram), &length) ==
                    ESP_OK &&
                send_datagram_unlocked(datagram, length, &s_server_address);
    xSemaphoreGive(s_tx_mutex);
    return sent;
}

static void send_csi(const wcsi_csi_queue_item_t *item)
{
    uint8_t datagram[WCSI_MAX_DATAGRAM];
    size_t length;
    const wcsi_csi_metadata_t *metadata = &item->metadata;
    wcsi_csi_t csi = {
        .rssi = metadata->rssi,
        .noise_floor = metadata->noise_floor,
        .channel = metadata->channel,
        .secondary_channel = metadata->secondary_channel,
        .rx_timestamp = metadata->rx_timestamp,
        .signal_length = metadata->signal_length,
        .rate = metadata->rate,
        .signal_mode = metadata->signal_mode,
        .mcs = metadata->mcs,
        .bandwidth = metadata->bandwidth,
        .phy_flags = metadata->phy_flags,
        .antenna = metadata->antenna,
        .rx_state = metadata->rx_state,
        .first_word_invalid = metadata->first_word_invalid != 0,
        .iq = item->iq,
        .iq_length = metadata->csi_length,
    };
    memcpy(csi.source_mac, metadata->source_mac, sizeof(csi.source_mac));
    memcpy(csi.destination_mac, metadata->destination_mac, sizeof(csi.destination_mac));
    xSemaphoreTake(s_tx_mutex, portMAX_DELAY);
    wcsi_header_t header = next_header(WCSI_MESSAGE_CSI_FRAME);
    bool sent = wcsi_encode_csi(&header, &csi, datagram, sizeof(datagram), &length) == ESP_OK &&
                send_datagram_unlocked(datagram, length, &s_server_address);
    xSemaphoreGive(s_tx_mutex);
    if (sent) {
        locked_increment(&s_csi_sent);
    }
}

static void telemetry_task(void *argument)
{
    (void)argument;
    int64_t last_hello_us = 0;
    int64_t last_state_us = 0;
    int64_t last_heartbeat_us = 0;
    ESP_LOGI(TAG, "UDP telemetry task started");
    while (true) {
        wcsi_csi_queue_item_t item;
        if (xQueueReceive(s_csi_queue, &item, pdMS_TO_TICKS(10)) == pdTRUE &&
            node_is_connected()) {
            send_csi(&item);
        }

        if (!node_is_connected()) {
            continue;
        }
        wcsi_control_item_t control;
        while (xQueueReceive(s_control_queue, &control, 0) == pdTRUE) {
            send_sensing(control.reason, control.has_stable_override,
                         control.stable_state);
        }

        int64_t now_us = esp_timer_get_time();
        bool hello_requested;
        portENTER_CRITICAL(&s_lock);
        hello_requested = s_hello_requested;
        s_hello_requested = false;
        portEXIT_CRITICAL(&s_lock);
        if (hello_requested || last_hello_us == 0 ||
            now_us - last_hello_us >= (int64_t)CONFIG_WCSI_HELLO_PERIOD_MS * 1000) {
            send_hello();
            last_hello_us = now_us;
        }
        if (last_state_us == 0 ||
            now_us - last_state_us >= (int64_t)CONFIG_WCSI_STATE_PERIOD_MS * 1000) {
            send_sensing(WCSI_STATE_REASON_PERIODIC, false, WCSI_STATE_UNKNOWN);
            last_state_us = now_us;
        }
        if (last_heartbeat_us == 0 ||
            now_us - last_heartbeat_us >= (int64_t)CONFIG_WCSI_HEARTBEAT_PERIOD_MS * 1000) {
            send_heartbeat();
            last_heartbeat_us = now_us;
        }
    }
}

static wcsi_ack_status_t ack_status_for_error(esp_err_t error)
{
    if (error == ESP_OK) {
        return WCSI_ACK_OK;
    }
    if (error == ESP_ERR_INVALID_ARG || error == ESP_ERR_INVALID_SIZE) {
        return WCSI_ACK_INVALID_ARGUMENT;
    }
    if (error == ESP_ERR_INVALID_STATE || error == ESP_ERR_NOT_FOUND) {
        return WCSI_ACK_NOT_READY;
    }
    if (error == ESP_ERR_NOT_SUPPORTED) {
        return WCSI_ACK_UNSUPPORTED;
    }
    return WCSI_ACK_INTERNAL_ERROR;
}

static wcsi_ack_cache_entry_t *find_cached_ack(uint32_t boot_id, uint32_t correlation_id)
{
    for (size_t index = 0; index < WCSI_ACK_CACHE_CAPACITY; ++index) {
        if (s_ack_cache[index].valid && s_ack_cache[index].boot_id == boot_id &&
            s_ack_cache[index].correlation_id == correlation_id) {
            return &s_ack_cache[index];
        }
    }
    return NULL;
}

static esp_err_t apply_command(const wcsi_command_t *command,
                               wcsi_sensing_config_t *response_config,
                               const wcsi_sensing_config_t **response_config_pointer)
{
    if (!node_is_connected()) {
        return ESP_ERR_INVALID_STATE;
    }
    uint8_t ap_bssid[6];
    snapshot_ap_identity(ap_bssid, NULL);
    xSemaphoreTake(s_sensing_mutex, portMAX_DELAY);
    esp_wifi_sensing_fsm_handle_t fsm;
    bool sensing_ready;
    portENTER_CRITICAL(&s_lock);
    fsm = s_fsm;
    sensing_ready = s_sensing_ready;
    portEXIT_CRITICAL(&s_lock);
    if (fsm == NULL || !sensing_ready) {
        xSemaphoreGive(s_sensing_mutex);
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t error;
    if (command->opcode == WCSI_COMMAND_RESET_BASELINE) {
        error = esp_wifi_sensing_fsm_control(
            fsm, ESP_WIFI_SENSING_FSM_CTRL_RESET_BASELINE, NULL);
        xSemaphoreGive(s_sensing_mutex);
        if (error == ESP_OK) {
            wcsi_control_item_t item = {.reason = WCSI_STATE_REASON_RESET};
            xQueueSend(s_control_queue, &item, 0);
        }
        return error;
    }
    if (command->opcode == WCSI_COMMAND_GET_CONFIG) {
        esp_wifi_sensing_fsm_channel_config_t config;
        error = esp_wifi_sensing_fsm_get_channel_config(fsm, ap_bssid, &config);
        if (error == ESP_OK) {
            copy_public_config(response_config, &config);
            *response_config_pointer = response_config;
        }
        xSemaphoreGive(s_sensing_mutex);
        return error;
    }
    if (command->opcode == WCSI_COMMAND_SET_CONFIG) {
        if (!isfinite(command->config.motion_sensitivity) ||
            !isfinite(command->config.presence_sensitivity) ||
            !isfinite(command->config.active_jitter_min) ||
            command->config.motion_sensitivity <= 0.0f ||
            command->config.motion_sensitivity > 1.0f ||
            command->config.presence_sensitivity < 0.0f ||
            command->config.presence_sensitivity > 1.0f ||
            command->config.active_jitter_min < 0.0f ||
            command->config.active_filter_ms > 60000U) {
            xSemaphoreGive(s_sensing_mutex);
            return ESP_ERR_INVALID_ARG;
        }
        esp_wifi_sensing_fsm_channel_config_t config = {
            .sensitivity = command->config.motion_sensitivity,
            .presence_sensitivity = command->config.presence_sensitivity,
            .active_jitter_min = command->config.active_jitter_min,
            .active_filter_ms = command->config.active_filter_ms,
        };
        error = esp_wifi_sensing_fsm_set_channel_config(fsm, ap_bssid, &config);
        if (error == ESP_OK) {
            copy_public_config(response_config, &config);
            *response_config_pointer = response_config;
        }
        xSemaphoreGive(s_sensing_mutex);
        return error;
    }
    xSemaphoreGive(s_sensing_mutex);
    return ESP_ERR_NOT_SUPPORTED;
}

static void handle_command(const uint8_t *datagram, size_t length,
                           const struct sockaddr_in *source)
{
    wcsi_header_t command_header;
    wcsi_command_t command;
    if (wcsi_decode_command(datagram, length, &command_header, &command) != ESP_OK ||
        memcmp(command_header.node_id, s_node_id, sizeof(s_node_id)) != 0 ||
        command_header.boot_id != s_boot_id) {
        return;
    }
    wcsi_ack_cache_entry_t *cached = find_cached_ack(command_header.boot_id,
                                                     command.correlation_id);
    if (cached != NULL) {
        send_cached_datagram_to(cached->datagram, cached->length, source);
        return;
    }

    wcsi_sensing_config_t response_config;
    const wcsi_sensing_config_t *response_config_pointer = NULL;
    esp_err_t command_error = apply_command(&command, &response_config,
                                            &response_config_pointer);
    wcsi_ack_status_t status = ack_status_for_error(command_error);
    if (status != WCSI_ACK_OK) {
        response_config_pointer = NULL;
    }
    wcsi_ack_t ack = {
        .correlation_id = command.correlation_id,
        .opcode = command.opcode,
        .status = status,
        .config = response_config_pointer,
    };
    xSemaphoreTake(s_tx_mutex, portMAX_DELAY);
    wcsi_header_t ack_header = next_header(WCSI_MESSAGE_COMMAND_ACK);
#if CONFIG_WCSI_TX_ORDER_FAULT_SELF_TEST
    if (!s_tx_delay_injected) {
        s_tx_delay_injected = true;
        ESP_LOGW(TAG, "TX order fault self-test delaying allocated ACK");
        vTaskDelay(pdMS_TO_TICKS(100));
    }
#endif
    wcsi_ack_cache_entry_t *entry = &s_ack_cache[s_ack_cache_next];
    size_t encoded_length;
    if (wcsi_encode_ack(&ack_header, &ack, entry->datagram, sizeof(entry->datagram),
                        &encoded_length) != ESP_OK) {
        xSemaphoreGive(s_tx_mutex);
        return;
    }
    entry->valid = true;
    entry->boot_id = command_header.boot_id;
    entry->correlation_id = command.correlation_id;
    entry->length = encoded_length;
    s_ack_cache_next = (s_ack_cache_next + 1U) % WCSI_ACK_CACHE_CAPACITY;
    send_datagram_unlocked(entry->datagram, entry->length, source);
    xSemaphoreGive(s_tx_mutex);
}

static void command_task(void *argument)
{
    (void)argument;
    uint8_t datagram[WCSI_MAX_DATAGRAM + 1U];
    ESP_LOGI(TAG, "UDP command task started on port %u", CONFIG_WCSI_LOCAL_PORT);
    while (true) {
        struct sockaddr_in source;
        socklen_t source_length = sizeof(source);
        int received = recvfrom(s_socket, datagram, sizeof(datagram), 0,
                                (struct sockaddr *)&source, &source_length);
        if (received > 0 && received <= WCSI_MAX_DATAGRAM) {
            handle_command(datagram, (size_t)received, &source);
        } else if (received < 0 && errno != EAGAIN && errno != EWOULDBLOCK) {
            vTaskDelay(pdMS_TO_TICKS(50));
        }
    }
}

static esp_err_t install_sensing_for_ap(const uint8_t ap_bssid[6])
{
    xSemaphoreTake(s_sensing_mutex, portMAX_DELAY);
    esp_wifi_sensing_fsm_handle_t old_fsm;
    portENTER_CRITICAL(&s_lock);
    old_fsm = s_fsm;
    s_fsm = NULL;
    s_sensing_ready = false;
    portEXIT_CRITICAL(&s_lock);
    if (old_fsm != NULL) {
        esp_err_t delete_error = esp_wifi_sensing_fsm_delete(old_fsm);
        if (delete_error != ESP_OK) {
            portENTER_CRITICAL(&s_lock);
            s_fsm = old_fsm;
            portEXIT_CRITICAL(&s_lock);
            ESP_LOGE(TAG, "old sensing FSM cleanup failed: %s",
                     esp_err_to_name(delete_error));
            xSemaphoreGive(s_sensing_mutex);
            return delete_error;
        }
    }

    esp_wifi_sensing_fsm_handle_t new_fsm = NULL;
    esp_wifi_sensing_fsm_config_t fsm_config = DEFAULT_ESP_WIFI_SENSING_FSM_CONFIG();
    fsm_config.max_channel_num = 1;
    esp_err_t error = esp_wifi_sensing_fsm_create(&fsm_config, &new_fsm);
    if (error != ESP_OK) {
        goto cleanup;
    }

    esp_radar_config_t radar_config;
    error = esp_radar_get_config(&radar_config);
    if (error != ESP_OK) {
        goto cleanup;
    }
    radar_config.csi_config.csi_filtered_cb = wcsi_node_on_filtered_csi;
    radar_config.csi_config.csi_filtered_cb_ctx = NULL;
    error = esp_radar_change_config(&radar_config);
    if (error != ESP_OK) {
        goto cleanup;
    }

#if CONFIG_WCSI_SENSING_INIT_FAULT_SELF_TEST
    if (!s_sensing_failure_injected) {
        s_sensing_failure_injected = true;
        ESP_LOGW(TAG, "sensing fault self-test injecting post-create failure");
        error = ESP_FAIL;
        goto cleanup;
    }
#endif

    error = esp_wifi_sensing_fsm_add_channel(new_fsm, ap_bssid);
    if (error != ESP_OK) {
        goto cleanup;
    }
    error = esp_wifi_sensing_fsm_register_event_cb(
        new_fsm, ESP_WIFI_SENSING_FSM_EVENT_ACTIVE, wcsi_node_on_sensing_event, NULL);
    if (error != ESP_OK) {
        goto cleanup;
    }
    error = esp_wifi_sensing_fsm_register_event_cb(
        new_fsm, ESP_WIFI_SENSING_FSM_EVENT_INACTIVE, wcsi_node_on_sensing_event, NULL);
    if (error != ESP_OK) {
        goto cleanup;
    }
    error = esp_wifi_sensing_fsm_control(new_fsm, ESP_WIFI_SENSING_FSM_CTRL_START, NULL);
    if (error != ESP_OK) {
        goto cleanup;
    }
    error = esp_wifi_sensing_fsm_control(
        new_fsm, ESP_WIFI_SENSING_FSM_CTRL_RESET_BASELINE, NULL);
    if (error != ESP_OK) {
        goto cleanup;
    }
    esp_err_t ping_error = esp_wifi_sensing_fsm_ping_router_start(new_fsm);
    if (ping_error == ESP_OK) {
        ESP_LOGI(TAG, "router ping started");
    } else {
        ESP_LOGW(TAG, "router ping start failed: %s", esp_err_to_name(ping_error));
    }
    if (!ap_identity_matches(ap_bssid)) {
        error = ESP_ERR_INVALID_STATE;
        goto cleanup;
    }
    portENTER_CRITICAL(&s_lock);
    s_fsm = new_fsm;
    s_sensing_ready = true;
    portEXIT_CRITICAL(&s_lock);
    new_fsm = NULL;
    ESP_LOGI(TAG, "official sensing FSM started for AP channel");

cleanup:
    if (new_fsm != NULL) {
        esp_err_t delete_error = esp_wifi_sensing_fsm_delete(new_fsm);
        if (delete_error != ESP_OK) {
            portENTER_CRITICAL(&s_lock);
            s_fsm = new_fsm;
            portEXIT_CRITICAL(&s_lock);
            new_fsm = NULL;
            ESP_LOGE(TAG, "partial sensing FSM rollback deferred: %s",
                     esp_err_to_name(delete_error));
        }
    }
    xSemaphoreGive(s_sensing_mutex);
    return error;
}

static void sensing_retry_task(void *argument)
{
    (void)argument;
    size_t retry_index = 0;
    while (node_is_connected()) {
        size_t delay_index = retry_index;
        if (delay_index >= sizeof(RECONNECT_DELAYS_MS) / sizeof(RECONNECT_DELAYS_MS[0])) {
            delay_index = sizeof(RECONNECT_DELAYS_MS) / sizeof(RECONNECT_DELAYS_MS[0]) - 1U;
        }
        vTaskDelay(pdMS_TO_TICKS(RECONNECT_DELAYS_MS[delay_index]));
        if (!node_is_connected()) {
            break;
        }
        wifi_ap_record_t ap_info = {0};
        esp_err_t error = esp_wifi_sta_get_ap_info(&ap_info);
        if (error == ESP_OK) {
            error = install_sensing_for_ap(ap_info.bssid);
        }
        if (error != ESP_OK) {
            ESP_LOGW(TAG, "sensing retry %lu failed: %s", (unsigned long)(retry_index + 1U),
                     esp_err_to_name(error));
            ++retry_index;
            continue;
        }
        ESP_LOGI(TAG, "sensing initialization recovered on retry %lu",
                 (unsigned long)(retry_index + 1U));
#if CONFIG_WCSI_SENSING_INIT_FAULT_SELF_TEST
        ESP_LOGI(TAG, "sensing fault self-test retry PASS");
#endif
        break;
    }
    portENTER_CRITICAL(&s_lock);
    s_sensing_retry_task = NULL;
    portEXIT_CRITICAL(&s_lock);
    vTaskDelete(NULL);
}

static void schedule_sensing_retry(void)
{
    portENTER_CRITICAL(&s_lock);
    bool start_task = s_sensing_retry_task == NULL;
    if (start_task) {
        s_sensing_retry_task = (TaskHandle_t)1;
    }
    portEXIT_CRITICAL(&s_lock);
    if (!start_task) {
        return;
    }
    TaskHandle_t task = NULL;
    if (xTaskCreate(sensing_retry_task, "wcsi_sensing_retry", 4096,
                    NULL, 5, &task) != pdPASS) {
        portENTER_CRITICAL(&s_lock);
        s_sensing_retry_task = NULL;
        portEXIT_CRITICAL(&s_lock);
        ESP_LOGE(TAG, "cannot create sensing retry task");
        return;
    }
    portENTER_CRITICAL(&s_lock);
    s_sensing_retry_task = task;
    portEXIT_CRITICAL(&s_lock);
}

static void reconnect_task(void *argument)
{
    uint32_t delay_ms = (uint32_t)(uintptr_t)argument;
    vTaskDelay(pdMS_TO_TICKS(delay_ms));
    portENTER_CRITICAL(&s_lock);
    s_reconnect_task = NULL;
    bool connected = s_connected;
    portEXIT_CRITICAL(&s_lock);
    if (!connected) {
        esp_err_t error = esp_wifi_connect();
        if (error != ESP_OK) {
            ESP_LOGW(TAG, "Wi-Fi reconnect request failed: %s", esp_err_to_name(error));
        }
    }
    vTaskDelete(NULL);
}

#if CONFIG_WCSI_RECONNECT_SELF_TEST
static void reconnect_self_test_task(void *argument)
{
    (void)argument;
    vTaskDelay(pdMS_TO_TICKS(CONFIG_WCSI_RECONNECT_SELF_TEST_DELAY_MS));
    ESP_LOGI(TAG, "reconnect self-test disconnecting once");
    esp_wifi_disconnect();
    vTaskDelete(NULL);
}
#endif

void wcsi_node_on_filtered_csi(void *ctx, const wifi_csi_filtered_info_t *filtered)
{
    (void)ctx;
    uint8_t ap_bssid[6];
    snapshot_ap_identity(ap_bssid, NULL);
    if (filtered == NULL || filtered->info == NULL || filtered->raw_data == NULL ||
        filtered->raw_len == 0 || filtered->raw_len > WCSI_MAX_CSI_LENGTH ||
        memcmp(filtered->info->mac, ap_bssid, sizeof(ap_bssid)) != 0 ||
        s_csi_queue == NULL) {
        return;
    }
    const wifi_csi_info_t *info = filtered->info;
    const wifi_pkt_rx_ctrl_t *rx = &info->rx_ctrl;
    wcsi_csi_queue_item_t item = {0};
    memcpy(item.metadata.source_mac, info->mac, sizeof(item.metadata.source_mac));
    memcpy(item.metadata.destination_mac, info->dmac,
           sizeof(item.metadata.destination_mac));
    item.metadata.rssi = rx->rssi;
    item.metadata.noise_floor = rx->noise_floor;
    item.metadata.channel = rx->channel;
    item.metadata.secondary_channel = rx->secondary_channel;
    item.metadata.rx_timestamp = rx->timestamp;
    item.metadata.signal_length = rx->sig_len;
    item.metadata.csi_length = filtered->raw_len;
    item.metadata.rate = rx->rate;
    item.metadata.signal_mode = rx->sig_mode;
    item.metadata.mcs = rx->mcs;
    item.metadata.bandwidth = rx->cwb;
    item.metadata.phy_flags = (uint16_t)(rx->smoothing | (rx->not_sounding << 1) |
                                         (rx->aggregation << 2) | ((rx->stbc != 0) << 3) |
                                         (rx->fec_coding << 4) | (rx->sgi << 5));
    item.metadata.antenna = rx->ant;
    item.metadata.rx_state = rx->rx_state;
    item.metadata.first_word_invalid = info->first_word_invalid ? 1 : 0;
    memcpy(item.iq, filtered->raw_data, filtered->raw_len);
    enqueue_csi(s_csi_queue, &item, &s_csi_accepted, &s_queue_dropped);
}

void wcsi_node_on_sensing_event(esp_wifi_sensing_fsm_handle_t handle,
                                const uint8_t peer_mac[6],
                                esp_wifi_sensing_fsm_event_t event,
                                uint32_t data,
                                void *user_data)
{
    (void)handle;
    (void)data;
    (void)user_data;
    uint8_t ap_bssid[6];
    snapshot_ap_identity(ap_bssid, NULL);
    if (peer_mac == NULL || memcmp(peer_mac, ap_bssid, sizeof(ap_bssid)) != 0 ||
        s_control_queue == NULL) {
        return;
    }
    wcsi_control_item_t item = {
        .reason = event == ESP_WIFI_SENSING_FSM_EVENT_ACTIVE ? WCSI_STATE_REASON_ACTIVE
                                                             : WCSI_STATE_REASON_INACTIVE,
        .stable_state = event == ESP_WIFI_SENSING_FSM_EVENT_ACTIVE ? 1 : 0,
        .has_stable_override = true,
    };
    xQueueSend(s_control_queue, &item, 0);
}

void wcsi_node_on_disconnected(void)
{
    uint32_t delay_ms;
    portENTER_CRITICAL(&s_lock);
    s_connected = false;
    s_sensing_ready = false;
    size_t delay_index = s_reconnect_attempt;
    if (delay_index >= sizeof(RECONNECT_DELAYS_MS) / sizeof(RECONNECT_DELAYS_MS[0])) {
        delay_index = sizeof(RECONNECT_DELAYS_MS) / sizeof(RECONNECT_DELAYS_MS[0]) - 1U;
    }
    delay_ms = RECONNECT_DELAYS_MS[delay_index];
    ++s_reconnect_attempt;
    bool start_task = s_reconnect_task == NULL;
    portEXIT_CRITICAL(&s_lock);
    if (s_csi_queue != NULL) {
        xQueueReset(s_csi_queue);
    }
    xSemaphoreTake(s_sensing_mutex, portMAX_DELAY);
    esp_wifi_sensing_fsm_handle_t fsm;
    portENTER_CRITICAL(&s_lock);
    fsm = s_fsm;
    s_fsm = NULL;
    portEXIT_CRITICAL(&s_lock);
    if (fsm != NULL) {
        esp_err_t delete_error = esp_wifi_sensing_fsm_delete(fsm);
        if (delete_error != ESP_OK) {
            portENTER_CRITICAL(&s_lock);
            s_fsm = fsm;
            portEXIT_CRITICAL(&s_lock);
            ESP_LOGE(TAG, "disconnected sensing FSM cleanup failed: %s",
                     esp_err_to_name(delete_error));
        }
    }
    xSemaphoreGive(s_sensing_mutex);
    if (start_task) {
        if (xTaskCreate(reconnect_task, "wcsi_reconnect", 3072,
                        (void *)(uintptr_t)delay_ms, 5, &s_reconnect_task) != pdPASS) {
            portENTER_CRITICAL(&s_lock);
            s_reconnect_task = NULL;
            portEXIT_CRITICAL(&s_lock);
        } else {
            ESP_LOGW(TAG, "Wi-Fi disconnected; reconnect in %lu ms",
                     (unsigned long)delay_ms);
        }
    }
}

void wcsi_node_on_connected(void)
{
    wifi_ap_record_t ap_info = {0};
    esp_err_t error = esp_wifi_sta_get_ap_info(&ap_info);
    if (error != ESP_OK) {
        ESP_LOGE(TAG, "cannot read connected AP: %s", esp_err_to_name(error));
        return;
    }
    portENTER_CRITICAL(&s_lock);
    memcpy(s_ap_bssid, ap_info.bssid, sizeof(s_ap_bssid));
    s_ap_channel = ap_info.primary;
    s_connected = true;
    s_sensing_ready = false;
    s_reconnect_attempt = 0;
    s_hello_requested = true;
    portEXIT_CRITICAL(&s_lock);
    wcsi_control_item_t item = {.reason = WCSI_STATE_REASON_RECONNECT};
    xQueueSend(s_control_queue, &item, 0);
    ESP_LOGI(TAG, "IPv4 ready; AP channel=%u", ap_info.primary);

    error = install_sensing_for_ap(ap_info.bssid);
    if (error != ESP_OK) {
        ESP_LOGE(TAG, "sensing initialization failed: %s", esp_err_to_name(error));
        schedule_sensing_retry();
    }

#if CONFIG_WCSI_RECONNECT_SELF_TEST
    static bool reconnect_test_started;
    if (!reconnect_test_started) {
        reconnect_test_started = true;
        xTaskCreate(reconnect_self_test_task, "wcsi_reconnect_test", 2048, NULL, 4, NULL);
    }
#endif
}

#if CONFIG_WIFICSI_PROTOCOL_SELF_TEST
esp_err_t wcsi_node_queue_self_test(void)
{
    QueueHandle_t queue = xQueueCreate(2, sizeof(wcsi_csi_queue_item_t));
    if (queue == NULL) {
        return ESP_ERR_NO_MEM;
    }
    wcsi_csi_queue_item_t synthetic = {0};
    synthetic.metadata.csi_length = 8;
    for (size_t index = 0; index < 8; ++index) {
        synthetic.iq[index] = (uint8_t)index;
    }
    uint32_t accepted = 0;
    uint32_t queue_dropped = 0;
    bool first = enqueue_csi(queue, &synthetic, &accepted, &queue_dropped);
    bool second = enqueue_csi(queue, &synthetic, &accepted, &queue_dropped);
    bool overflow = enqueue_csi(queue, &synthetic, &accepted, &queue_dropped);
    bool passed = first && second && !overflow && accepted == 2 && queue_dropped == 1 &&
                  uxQueueMessagesWaiting(queue) == 2;
    vQueueDelete(queue);
    return passed ? ESP_OK : ESP_FAIL;
}
#endif

esp_err_t wcsi_node_init(void)
{
    if (CONFIG_WCSI_WIFI_SSID[0] == '\0') {
        ESP_LOGE(TAG, "Wi-Fi SSID is not configured in local sdkconfig");
        return ESP_ERR_INVALID_STATE;
    }
    do {
        s_boot_id = esp_random();
    } while (s_boot_id == 0);
    ESP_ERROR_CHECK(esp_read_mac(s_node_id, ESP_MAC_WIFI_STA));

    size_t storage_size = (size_t)CONFIG_WCSI_QUEUE_CAPACITY * sizeof(wcsi_csi_queue_item_t);
    s_csi_queue_storage = heap_caps_malloc(storage_size,
                                           MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (s_csi_queue_storage == NULL) {
        return ESP_ERR_NO_MEM;
    }
    s_csi_queue = xQueueCreateStatic(CONFIG_WCSI_QUEUE_CAPACITY,
                                     sizeof(wcsi_csi_queue_item_t),
                                     s_csi_queue_storage, &s_csi_queue_state);
    s_control_queue = xQueueCreate(WCSI_CONTROL_QUEUE_CAPACITY,
                                   sizeof(wcsi_control_item_t));
    s_tx_mutex = xSemaphoreCreateMutex();
    s_sensing_mutex = xSemaphoreCreateMutex();
    if (s_csi_queue == NULL || s_control_queue == NULL || s_tx_mutex == NULL ||
        s_sensing_mutex == NULL) {
        return ESP_ERR_NO_MEM;
    }

    s_socket = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (s_socket < 0) {
        return ESP_FAIL;
    }
    struct timeval timeout = {.tv_sec = 0, .tv_usec = 250000};
    setsockopt(s_socket, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    struct sockaddr_in local_address = {
        .sin_family = AF_INET,
        .sin_port = htons(CONFIG_WCSI_LOCAL_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    if (bind(s_socket, (struct sockaddr *)&local_address, sizeof(local_address)) != 0) {
        return ESP_FAIL;
    }
    s_server_address.sin_family = AF_INET;
    s_server_address.sin_port = htons(CONFIG_WCSI_SERVER_PORT);
    if (inet_pton(AF_INET, CONFIG_WCSI_SERVER_ADDRESS,
                  &s_server_address.sin_addr) != 1) {
        return ESP_ERR_INVALID_ARG;
    }

    if (xTaskCreate(telemetry_task, "wcsi_udp_tx", 8192, NULL, 8, NULL) != pdPASS ||
        xTaskCreate(command_task, "wcsi_udp_rx", 6144, NULL, 7, NULL) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }

    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init_config = WIFI_INIT_CONFIG_DEFAULT();
    esp_err_t error = esp_wifi_init(&init_config);
    if (error != ESP_OK) {
        return error;
    }
    wifi_config_t wifi_config = {0};
    strlcpy((char *)wifi_config.sta.ssid, CONFIG_WCSI_WIFI_SSID,
            sizeof(wifi_config.sta.ssid));
    strlcpy((char *)wifi_config.sta.password, CONFIG_WCSI_WIFI_PASSWORD,
            sizeof(wifi_config.sta.password));
    wifi_config.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    wifi_config.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    wifi_config.sta.threshold.rssi = -127;
    wifi_config.sta.threshold.authmode = WIFI_AUTH_OPEN;
    error = esp_wifi_set_mode(WIFI_MODE_STA);
    if (error == ESP_OK) {
        error = esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    }
    if (error == ESP_OK) {
        error = esp_wifi_start();
    }
    if (error == ESP_OK) {
        error = esp_wifi_set_ps(WIFI_PS_NONE);
    }
    if (error == ESP_OK) {
        error = esp_wifi_connect();
    }
    if (error == ESP_OK) {
        ESP_LOGI(TAG, "WCSI runtime initialized; Wi-Fi connection requested");
    }
    return error;
}
