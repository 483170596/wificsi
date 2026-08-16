#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "esp_radar.h"
#include "esp_wifi_sensing.h"

#include "wcsi_protocol.h"

#ifdef __cplusplus
extern "C" {
#endif

enum {
    WCSI_NODE_HELLO_PAYLOAD_SIZE = 24,
    WCSI_NODE_CSI_METADATA_SIZE = 36,
    WCSI_NODE_SENSING_PAYLOAD_SIZE = 56,
    WCSI_NODE_HEARTBEAT_PAYLOAD_SIZE = 32,
    WCSI_NODE_COMMAND_PREFIX_SIZE = 8,
    WCSI_NODE_ACK_PREFIX_SIZE = 8,
    WCSI_NODE_CONFIG_PAYLOAD_SIZE = 16,
};

_Static_assert(WCSI_HEADER_SIZE == 40U, "WCSI header wire size changed");
_Static_assert(WCSI_NODE_HELLO_PAYLOAD_SIZE == 24, "HELLO payload wire size changed");
_Static_assert(WCSI_NODE_CSI_METADATA_SIZE == 36, "CSI metadata wire size changed");
_Static_assert(WCSI_NODE_SENSING_PAYLOAD_SIZE == 56, "sensing payload wire size changed");
_Static_assert(WCSI_NODE_HEARTBEAT_PAYLOAD_SIZE == 32, "heartbeat payload wire size changed");
_Static_assert(WCSI_NODE_COMMAND_PREFIX_SIZE == 8, "command prefix wire size changed");
_Static_assert(WCSI_NODE_ACK_PREFIX_SIZE == 8, "ACK prefix wire size changed");
_Static_assert(sizeof(wcsi_sensing_config_t) == WCSI_NODE_CONFIG_PAYLOAD_SIZE,
               "sensing config model must remain four 32-bit fields");
_Static_assert(WCSI_MAX_CSI_LENGTH + WCSI_HEADER_SIZE + WCSI_NODE_CSI_METADATA_SIZE ==
                   WCSI_MAX_DATAGRAM,
               "maximum CSI datagram must exactly fit the protocol limit");

esp_err_t wcsi_node_init(void);
void wcsi_node_on_filtered_csi(void *ctx, const wifi_csi_filtered_info_t *filtered);
void wcsi_node_on_sensing_event(esp_wifi_sensing_fsm_handle_t handle,
                                const uint8_t peer_mac[6],
                                esp_wifi_sensing_fsm_event_t event,
                                uint32_t data,
                                void *user_data);
void wcsi_node_on_connected(void);
void wcsi_node_on_disconnected(void);

#if CONFIG_WIFICSI_PROTOCOL_SELF_TEST
esp_err_t wcsi_node_queue_self_test(void);
#endif

#ifdef __cplusplus
}
#endif
