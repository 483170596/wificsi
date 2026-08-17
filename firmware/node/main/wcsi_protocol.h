#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define WCSI_HEADER_SIZE 40U
#define WCSI_MAX_DATAGRAM 1200U
#define WCSI_MAX_CSI_LENGTH (WCSI_MAX_DATAGRAM - WCSI_HEADER_SIZE - 36U)

typedef enum {
    WCSI_MESSAGE_HELLO = 1,
    WCSI_MESSAGE_CSI_FRAME = 2,
    WCSI_MESSAGE_SENSING_STATE = 3,
    WCSI_MESSAGE_HEARTBEAT = 4,
    WCSI_MESSAGE_COMMAND = 5,
    WCSI_MESSAGE_COMMAND_ACK = 6,
} wcsi_message_type_t;

typedef enum {
    WCSI_COMMAND_GET_CONFIG = 1,
    WCSI_COMMAND_RESET_BASELINE = 2,
    WCSI_COMMAND_SET_CONFIG = 3,
} wcsi_command_opcode_t;

typedef enum {
    WCSI_ACK_OK = 0,
    WCSI_ACK_INVALID_ARGUMENT = 1,
    WCSI_ACK_NOT_READY = 2,
    WCSI_ACK_UNSUPPORTED = 3,
    WCSI_ACK_INTERNAL_ERROR = 4,
} wcsi_ack_status_t;

typedef struct {
    uint8_t node_id[6];
    uint32_t boot_id;
    uint32_t sequence;
    uint64_t device_time_us;
    wcsi_message_type_t message_type;
    uint8_t minor;
    uint8_t flags;
} wcsi_header_t;

typedef struct {
    float motion_sensitivity;
    float presence_sensitivity;
    float active_jitter_min;
    uint32_t active_filter_ms;
} wcsi_sensing_config_t;

typedef struct {
    uint16_t chip_model;
    uint8_t firmware_major;
    uint8_t firmware_minor;
    uint8_t firmware_patch;
    uint16_t capabilities;
    uint16_t max_csi_length;
    uint16_t queue_capacity;
    uint16_t heartbeat_interval_ms;
    uint16_t state_interval_ms;
    uint16_t listen_port;
    uint8_t ap_bssid[6];
} wcsi_hello_t;

typedef struct {
    uint8_t source_mac[6];
    uint8_t destination_mac[6];
    int8_t rssi;
    int8_t noise_floor;
    uint8_t channel;
    uint8_t secondary_channel;
    uint32_t rx_timestamp;
    uint16_t signal_length;
    uint8_t rate;
    uint8_t signal_mode;
    uint8_t mcs;
    uint8_t bandwidth;
    uint16_t phy_flags;
    uint8_t antenna;
    uint8_t rx_state;
    bool first_word_invalid;
    const uint8_t *iq;
    size_t iq_length;
} wcsi_csi_t;

typedef struct {
    uint8_t peer_mac[6];
    uint8_t stable_state;
    uint8_t process_state;
    uint8_t init_stage;
    uint8_t flags;
    uint8_t reason;
    float jitter;
    float wander;
    uint32_t smooth_scaled;
    uint32_t enter_level_scaled;
    uint32_t exit_level_scaled;
    float presence_wander_average;
    float presence_someone_threshold;
    wcsi_sensing_config_t config;
} wcsi_sensing_t;

typedef struct {
    uint64_t uptime_us;
    uint32_t free_heap;
    uint32_t minimum_free_heap;
    uint32_t csi_accepted;
    uint32_t csi_sent;
    uint32_t queue_dropped;
    uint32_t udp_send_errors;
} wcsi_heartbeat_t;

typedef struct {
    uint32_t correlation_id;
    wcsi_command_opcode_t opcode;
    bool has_config;
    wcsi_sensing_config_t config;
} wcsi_command_t;

typedef struct {
    uint32_t correlation_id;
    wcsi_command_opcode_t opcode;
    wcsi_ack_status_t status;
    const wcsi_sensing_config_t *config;
} wcsi_ack_t;

esp_err_t wcsi_encode_header(const wcsi_header_t *header, uint16_t payload_length,
                             uint8_t *output, size_t capacity);
esp_err_t wcsi_encode_hello(const wcsi_header_t *header, const wcsi_hello_t *payload,
                            uint8_t *output, size_t capacity, size_t *output_length);
esp_err_t wcsi_encode_csi(const wcsi_header_t *header, const wcsi_csi_t *payload,
                          uint8_t *output, size_t capacity, size_t *output_length);
esp_err_t wcsi_encode_sensing(const wcsi_header_t *header, const wcsi_sensing_t *payload,
                              uint8_t *output, size_t capacity, size_t *output_length);
esp_err_t wcsi_encode_heartbeat(const wcsi_header_t *header, const wcsi_heartbeat_t *payload,
                                uint8_t *output, size_t capacity, size_t *output_length);
esp_err_t wcsi_decode_command(const uint8_t *datagram, size_t length,
                              wcsi_header_t *header, wcsi_command_t *command);
esp_err_t wcsi_encode_ack(const wcsi_header_t *header, const wcsi_ack_t *payload,
                          uint8_t *output, size_t capacity, size_t *output_length);
esp_err_t wcsi_protocol_self_test(void);

#ifdef __cplusplus
}
#endif
