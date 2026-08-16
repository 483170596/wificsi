#include "wcsi_protocol.h"

#include <string.h>

#include "esp_crc.h"
#include "esp_log.h"

#include "wcsi_vectors.h"

#define WCSI_HELLO_SIZE 24U
#define WCSI_CSI_PREFIX_SIZE 36U
#define WCSI_SENSING_SIZE 56U
#define WCSI_HEARTBEAT_SIZE 32U
#define WCSI_COMMAND_PREFIX_SIZE 8U
#define WCSI_ACK_PREFIX_SIZE 8U
#define WCSI_CONFIG_SIZE 16U
#define WCSI_CRC_OFFSET 36U

static const char *TAG = "wcsi_protocol";

static void put_u16(uint8_t *output, uint16_t value)
{
    output[0] = (uint8_t)(value >> 8);
    output[1] = (uint8_t)value;
}

static void put_u32(uint8_t *output, uint32_t value)
{
    output[0] = (uint8_t)(value >> 24);
    output[1] = (uint8_t)(value >> 16);
    output[2] = (uint8_t)(value >> 8);
    output[3] = (uint8_t)value;
}

static void put_u64(uint8_t *output, uint64_t value)
{
    put_u32(output, (uint32_t)(value >> 32));
    put_u32(output + 4, (uint32_t)value);
}

static uint16_t get_u16(const uint8_t *input)
{
    return ((uint16_t)input[0] << 8) | input[1];
}

static uint32_t get_u32(const uint8_t *input)
{
    return ((uint32_t)input[0] << 24) | ((uint32_t)input[1] << 16) |
           ((uint32_t)input[2] << 8) | input[3];
}

static uint64_t get_u64(const uint8_t *input)
{
    return ((uint64_t)get_u32(input) << 32) | get_u32(input + 4);
}

static void put_float(uint8_t *output, float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    put_u32(output, bits);
}

static float get_float(const uint8_t *input)
{
    uint32_t bits = get_u32(input);
    float value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static uint32_t datagram_crc(const uint8_t *datagram, size_t length)
{
    static const uint8_t zero_crc[4] = {0};
    uint32_t crc = esp_crc32_le(0, datagram, WCSI_CRC_OFFSET);
    crc = esp_crc32_le(crc, zero_crc, sizeof(zero_crc));
    if (length > WCSI_HEADER_SIZE) {
        crc = esp_crc32_le(crc, datagram + WCSI_HEADER_SIZE,
                           (uint32_t)(length - WCSI_HEADER_SIZE));
    }
    return crc;
}

static bool valid_message_type(wcsi_message_type_t message_type)
{
    return message_type >= WCSI_MESSAGE_HELLO && message_type <= WCSI_MESSAGE_COMMAND_ACK;
}

static bool valid_opcode(uint8_t opcode)
{
    return opcode >= WCSI_COMMAND_GET_CONFIG && opcode <= WCSI_COMMAND_SET_CONFIG;
}

static esp_err_t require_packet(const void *header, const void *payload,
                                uint8_t *output, size_t capacity, size_t length,
                                size_t *output_length)
{
    if (header == NULL || payload == NULL || output == NULL || output_length == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (length > WCSI_MAX_DATAGRAM || capacity < length) {
        return ESP_ERR_INVALID_SIZE;
    }
    return ESP_OK;
}

static void encode_config(uint8_t *output, const wcsi_sensing_config_t *config)
{
    put_float(output, config->motion_sensitivity);
    put_float(output + 4, config->presence_sensitivity);
    put_float(output + 8, config->active_jitter_min);
    put_u32(output + 12, config->active_filter_ms);
}

static void decode_config(const uint8_t *input, wcsi_sensing_config_t *config)
{
    config->motion_sensitivity = get_float(input);
    config->presence_sensitivity = get_float(input + 4);
    config->active_jitter_min = get_float(input + 8);
    config->active_filter_ms = get_u32(input + 12);
}

esp_err_t wcsi_encode_header(const wcsi_header_t *header, uint16_t payload_length,
                             uint8_t *output, size_t capacity)
{
    if (header == NULL || output == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    size_t packet_length = (size_t)payload_length + WCSI_HEADER_SIZE;
    if (packet_length > WCSI_MAX_DATAGRAM || capacity < packet_length) {
        return ESP_ERR_INVALID_SIZE;
    }
    if (header->boot_id == 0 || header->minor != 0 || header->flags != 0 ||
        !valid_message_type(header->message_type)) {
        return ESP_ERR_INVALID_ARG;
    }

    memset(output, 0, WCSI_HEADER_SIZE);
    memcpy(output, "WCSI", 4);
    output[4] = 1;
    output[5] = header->minor;
    output[6] = (uint8_t)header->message_type;
    output[7] = header->flags;
    put_u16(output + 8, WCSI_HEADER_SIZE);
    put_u16(output + 10, payload_length);
    memcpy(output + 12, header->node_id, sizeof(header->node_id));
    put_u32(output + 20, header->boot_id);
    put_u32(output + 24, header->sequence);
    put_u64(output + 28, header->device_time_us);
    return ESP_OK;
}

static esp_err_t finish_packet(const wcsi_header_t *header, uint16_t payload_length,
                               uint8_t *output, size_t capacity, size_t *output_length)
{
    esp_err_t error = wcsi_encode_header(header, payload_length, output, capacity);
    if (error != ESP_OK) {
        return error;
    }
    size_t length = WCSI_HEADER_SIZE + payload_length;
    put_u32(output + WCSI_CRC_OFFSET, datagram_crc(output, length));
    *output_length = length;
    return ESP_OK;
}

esp_err_t wcsi_encode_hello(const wcsi_header_t *header, const wcsi_hello_t *payload,
                            uint8_t *output, size_t capacity, size_t *output_length)
{
    esp_err_t error = require_packet(header, payload, output, capacity,
                                     WCSI_HEADER_SIZE + WCSI_HELLO_SIZE, output_length);
    if (error != ESP_OK) {
        return error;
    }
    if (header->message_type != WCSI_MESSAGE_HELLO) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t *body = output + WCSI_HEADER_SIZE;
    memset(body, 0, WCSI_HELLO_SIZE);
    put_u16(body, payload->chip_model);
    body[2] = payload->firmware_major;
    body[3] = payload->firmware_minor;
    body[4] = payload->firmware_patch;
    put_u16(body + 6, payload->capabilities);
    put_u16(body + 8, payload->max_csi_length);
    put_u16(body + 10, payload->queue_capacity);
    put_u16(body + 12, payload->heartbeat_interval_ms);
    put_u16(body + 14, payload->state_interval_ms);
    put_u16(body + 16, payload->listen_port);
    memcpy(body + 18, payload->ap_bssid, 6);
    return finish_packet(header, WCSI_HELLO_SIZE, output, capacity, output_length);
}

esp_err_t wcsi_encode_csi(const wcsi_header_t *header, const wcsi_csi_t *payload,
                          uint8_t *output, size_t capacity, size_t *output_length)
{
    if (payload == NULL || payload->iq == NULL || payload->iq_length == 0 ||
        payload->iq_length > WCSI_MAX_CSI_LENGTH || payload->iq_length > UINT16_MAX) {
        return ESP_ERR_INVALID_ARG;
    }
    size_t payload_length = WCSI_CSI_PREFIX_SIZE + payload->iq_length;
    esp_err_t error = require_packet(header, payload, output, capacity,
                                     WCSI_HEADER_SIZE + payload_length, output_length);
    if (error != ESP_OK) {
        return error;
    }
    if (header->message_type != WCSI_MESSAGE_CSI_FRAME) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t *body = output + WCSI_HEADER_SIZE;
    memset(body, 0, WCSI_CSI_PREFIX_SIZE);
    memcpy(body, payload->source_mac, 6);
    memcpy(body + 6, payload->destination_mac, 6);
    body[12] = (uint8_t)payload->rssi;
    body[13] = (uint8_t)payload->noise_floor;
    body[14] = payload->channel;
    body[15] = payload->secondary_channel;
    put_u32(body + 16, payload->rx_timestamp);
    put_u16(body + 20, payload->signal_length);
    put_u16(body + 22, (uint16_t)payload->iq_length);
    body[24] = payload->rate;
    body[25] = payload->signal_mode;
    body[26] = payload->mcs;
    body[27] = payload->bandwidth;
    put_u16(body + 28, payload->phy_flags);
    body[30] = payload->antenna;
    body[31] = payload->rx_state;
    body[32] = payload->first_word_invalid ? 1 : 0;
    memcpy(body + WCSI_CSI_PREFIX_SIZE, payload->iq, payload->iq_length);
    return finish_packet(header, (uint16_t)payload_length, output, capacity, output_length);
}

esp_err_t wcsi_encode_sensing(const wcsi_header_t *header, const wcsi_sensing_t *payload,
                              uint8_t *output, size_t capacity, size_t *output_length)
{
    esp_err_t error = require_packet(header, payload, output, capacity,
                                     WCSI_HEADER_SIZE + WCSI_SENSING_SIZE, output_length);
    if (error != ESP_OK) {
        return error;
    }
    if (header->message_type != WCSI_MESSAGE_SENSING_STATE || payload->stable_state > 2 ||
        payload->process_state > 3 || payload->init_stage > 2) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t *body = output + WCSI_HEADER_SIZE;
    memset(body, 0, WCSI_SENSING_SIZE);
    memcpy(body, payload->peer_mac, 6);
    body[6] = payload->stable_state;
    body[7] = payload->process_state;
    body[8] = payload->init_stage;
    body[9] = payload->flags;
    body[10] = payload->reason;
    put_float(body + 12, payload->jitter);
    put_float(body + 16, payload->wander);
    put_u32(body + 20, payload->smooth_scaled);
    put_u32(body + 24, payload->enter_level_scaled);
    put_u32(body + 28, payload->exit_level_scaled);
    put_float(body + 32, payload->presence_wander_average);
    put_float(body + 36, payload->presence_someone_threshold);
    encode_config(body + 40, &payload->config);
    return finish_packet(header, WCSI_SENSING_SIZE, output, capacity, output_length);
}

esp_err_t wcsi_encode_heartbeat(const wcsi_header_t *header, const wcsi_heartbeat_t *payload,
                                uint8_t *output, size_t capacity, size_t *output_length)
{
    esp_err_t error = require_packet(header, payload, output, capacity,
                                     WCSI_HEADER_SIZE + WCSI_HEARTBEAT_SIZE, output_length);
    if (error != ESP_OK) {
        return error;
    }
    if (header->message_type != WCSI_MESSAGE_HEARTBEAT) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t *body = output + WCSI_HEADER_SIZE;
    put_u64(body, payload->uptime_us);
    put_u32(body + 8, payload->free_heap);
    put_u32(body + 12, payload->minimum_free_heap);
    put_u32(body + 16, payload->csi_accepted);
    put_u32(body + 20, payload->csi_sent);
    put_u32(body + 24, payload->queue_dropped);
    put_u32(body + 28, payload->udp_send_errors);
    return finish_packet(header, WCSI_HEARTBEAT_SIZE, output, capacity, output_length);
}

static esp_err_t validate_datagram(const uint8_t *datagram, size_t length,
                                   wcsi_header_t *header, uint16_t *header_length,
                                   uint16_t *payload_length)
{
    if (datagram == NULL || length < WCSI_HEADER_SIZE || length > WCSI_MAX_DATAGRAM) {
        return ESP_ERR_INVALID_SIZE;
    }
    if (memcmp(datagram, "WCSI", 4) != 0 || datagram[4] != 1 || datagram[7] != 0 ||
        !valid_message_type((wcsi_message_type_t)datagram[6])) {
        return ESP_ERR_INVALID_ARG;
    }
    uint16_t decoded_header_length = get_u16(datagram + 8);
    uint16_t decoded_payload_length = get_u16(datagram + 10);
    if (decoded_header_length < WCSI_HEADER_SIZE ||
        (datagram[5] == 0 && decoded_header_length != WCSI_HEADER_SIZE) ||
        (size_t)decoded_header_length + decoded_payload_length != length ||
        get_u32(datagram + 20) == 0) {
        return ESP_ERR_INVALID_SIZE;
    }
    uint32_t received_crc = get_u32(datagram + WCSI_CRC_OFFSET);
    if (datagram_crc(datagram, length) != received_crc) {
        return ESP_ERR_INVALID_CRC;
    }
    if (header != NULL) {
        memcpy(header->node_id, datagram + 12, 6);
        header->boot_id = get_u32(datagram + 20);
        header->sequence = get_u32(datagram + 24);
        header->device_time_us = get_u64(datagram + 28);
        header->message_type = (wcsi_message_type_t)datagram[6];
        header->minor = datagram[5];
        header->flags = datagram[7];
    }
    if (header_length != NULL) {
        *header_length = decoded_header_length;
    }
    if (payload_length != NULL) {
        *payload_length = decoded_payload_length;
    }
    return ESP_OK;
}

esp_err_t wcsi_decode_command(const uint8_t *datagram, size_t length,
                              wcsi_header_t *header, wcsi_command_t *command)
{
    if (header == NULL || command == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    uint16_t header_length;
    uint16_t payload_length;
    esp_err_t error = validate_datagram(datagram, length, header, &header_length, &payload_length);
    if (error != ESP_OK) {
        return error;
    }
    if (header->message_type != WCSI_MESSAGE_COMMAND ||
        (payload_length != WCSI_COMMAND_PREFIX_SIZE &&
         payload_length != WCSI_COMMAND_PREFIX_SIZE + WCSI_CONFIG_SIZE)) {
        return ESP_ERR_INVALID_SIZE;
    }
    const uint8_t *body = datagram + header_length;
    if (!valid_opcode(body[4])) {
        return ESP_ERR_INVALID_ARG;
    }
    uint32_t correlation_id = get_u32(body);
    if (header->sequence != correlation_id || header->device_time_us != 0) {
        return ESP_ERR_INVALID_ARG;
    }
    command->correlation_id = correlation_id;
    command->opcode = (wcsi_command_opcode_t)body[4];
    command->has_config = payload_length == WCSI_COMMAND_PREFIX_SIZE + WCSI_CONFIG_SIZE;
    if ((command->opcode == WCSI_COMMAND_SET_CONFIG) != command->has_config) {
        return ESP_ERR_INVALID_ARG;
    }
    if (command->has_config) {
        decode_config(body + WCSI_COMMAND_PREFIX_SIZE, &command->config);
    } else {
        memset(&command->config, 0, sizeof(command->config));
    }
    return ESP_OK;
}

esp_err_t wcsi_encode_ack(const wcsi_header_t *header, const wcsi_ack_t *payload,
                          uint8_t *output, size_t capacity, size_t *output_length)
{
    if (payload == NULL || !valid_opcode((uint8_t)payload->opcode) ||
        payload->status > WCSI_ACK_INTERNAL_ERROR) {
        return ESP_ERR_INVALID_ARG;
    }
    bool config_required = payload->status == WCSI_ACK_OK &&
                           (payload->opcode == WCSI_COMMAND_GET_CONFIG ||
                            payload->opcode == WCSI_COMMAND_SET_CONFIG);
    if ((payload->config != NULL) != config_required) {
        return ESP_ERR_INVALID_ARG;
    }
    size_t payload_length = WCSI_ACK_PREFIX_SIZE +
                            (payload->config == NULL ? 0 : WCSI_CONFIG_SIZE);
    esp_err_t error = require_packet(header, payload, output, capacity,
                                     WCSI_HEADER_SIZE + payload_length, output_length);
    if (error != ESP_OK) {
        return error;
    }
    if (header->message_type != WCSI_MESSAGE_COMMAND_ACK) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t *body = output + WCSI_HEADER_SIZE;
    memset(body, 0, payload_length);
    put_u32(body, payload->correlation_id);
    body[4] = (uint8_t)payload->opcode;
    body[5] = (uint8_t)payload->status;
    if (payload->config != NULL) {
        encode_config(body + WCSI_ACK_PREFIX_SIZE, payload->config);
    }
    return finish_packet(header, (uint16_t)payload_length, output, capacity, output_length);
}

static wcsi_header_t vector_header(uint32_t sequence, uint64_t time_us,
                                   wcsi_message_type_t message_type)
{
    wcsi_header_t header = {
        .node_id = {0x28, 0x84, 0x85, 0x87, 0x2b, 0xf4},
        .boot_id = 0x10203040,
        .sequence = sequence,
        .device_time_us = time_us,
        .message_type = message_type,
    };
    return header;
}

static esp_err_t expect_vector(const char *name, const uint8_t *actual, size_t actual_length,
                               const uint8_t *expected, size_t expected_length)
{
    if (actual_length != expected_length || memcmp(actual, expected, expected_length) != 0) {
        ESP_LOGE(TAG, "%s vector mismatch", name);
        return ESP_FAIL;
    }
    if (validate_datagram(expected, expected_length, NULL, NULL, NULL) != ESP_OK) {
        ESP_LOGE(TAG, "%s vector CRC validation failed", name);
        return ESP_FAIL;
    }
    return ESP_OK;
}

static void refresh_test_crc(uint8_t *datagram, size_t length)
{
    put_u32(datagram + WCSI_CRC_OFFSET, datagram_crc(datagram, length));
}

esp_err_t wcsi_protocol_self_test(void)
{
    uint8_t output[WCSI_MAX_DATAGRAM];
    size_t length;
    const uint8_t ap[6] = {0x5e, 0xe3, 0x88, 0xd9, 0x5b, 0x42};
    const wcsi_sensing_config_t config = {0.5f, 0.25f, 0.125f, 300};

    wcsi_header_t header = vector_header(1, 1000000, WCSI_MESSAGE_HELLO);
    wcsi_hello_t hello = {
        .chip_model = 1, .firmware_major = 1, .firmware_minor = 0, .firmware_patch = 0,
        .capabilities = 15, .max_csi_length = 612, .queue_capacity = 512,
        .heartbeat_interval_ms = 1000, .state_interval_ms = 1000, .listen_port = 5501,
    };
    memcpy(hello.ap_bssid, ap, 6);
    if (wcsi_encode_hello(&header, &hello, output, sizeof(output), &length) != ESP_OK ||
        expect_vector("HELLO", output, length, WCSI_VECTOR_HELLO, WCSI_VECTOR_HELLO_LEN) != ESP_OK) {
        return ESP_FAIL;
    }

    const uint8_t iq[] = {0, 1, 2, 3, 4, 5, 6, 7};
    header = vector_header(2, 2000000, WCSI_MESSAGE_CSI_FRAME);
    wcsi_csi_t csi = {
        .rssi = -80, .noise_floor = -95, .channel = 4, .rx_timestamp = 0x11223344,
        .signal_length = 128, .rate = 11, .signal_mode = 1, .mcs = 3,
        .phy_flags = 33, .iq = iq, .iq_length = sizeof(iq),
    };
    memcpy(csi.source_mac, ap, 6);
    memcpy(csi.destination_mac, header.node_id, 6);
    if (wcsi_encode_csi(&header, &csi, output, sizeof(output), &length) != ESP_OK ||
        expect_vector("CSI", output, length, WCSI_VECTOR_CSI_8, WCSI_VECTOR_CSI_8_LEN) != ESP_OK) {
        return ESP_FAIL;
    }

    header = vector_header(3, 3000000, WCSI_MESSAGE_SENSING_STATE);
    wcsi_sensing_t sensing = {
        .stable_state = 1, .process_state = 2, .init_stage = 2, .flags = 7, .reason = 1,
        .jitter = 0.5f, .wander = 0.25f, .smooth_scaled = 10,
        .enter_level_scaled = 20, .exit_level_scaled = 5,
        .presence_wander_average = 0.125f, .presence_someone_threshold = 0.25f,
        .config = config,
    };
    memcpy(sensing.peer_mac, ap, 6);
    if (wcsi_encode_sensing(&header, &sensing, output, sizeof(output), &length) != ESP_OK ||
        expect_vector("SENSING", output, length, WCSI_VECTOR_SENSING_ACTIVE,
                      WCSI_VECTOR_SENSING_ACTIVE_LEN) != ESP_OK) {
        return ESP_FAIL;
    }

    header = vector_header(4, 4000000, WCSI_MESSAGE_HEARTBEAT);
    wcsi_heartbeat_t heartbeat = {
        .uptime_us = 1000000, .free_heap = 300000, .minimum_free_heap = 250000,
        .csi_accepted = 50, .csi_sent = 49, .queue_dropped = 1,
    };
    if (wcsi_encode_heartbeat(&header, &heartbeat, output, sizeof(output), &length) != ESP_OK ||
        expect_vector("HEARTBEAT", output, length, WCSI_VECTOR_HEARTBEAT,
                      WCSI_VECTOR_HEARTBEAT_LEN) != ESP_OK) {
        return ESP_FAIL;
    }

    wcsi_command_t command;
    if (wcsi_decode_command(WCSI_VECTOR_GET_CONFIG, WCSI_VECTOR_GET_CONFIG_LEN,
                            &header, &command) != ESP_OK || command.correlation_id != 7 ||
        command.opcode != WCSI_COMMAND_GET_CONFIG || command.has_config ||
        expect_vector("GET_CONFIG", WCSI_VECTOR_GET_CONFIG, WCSI_VECTOR_GET_CONFIG_LEN,
                      WCSI_VECTOR_GET_CONFIG, WCSI_VECTOR_GET_CONFIG_LEN) != ESP_OK) {
        return ESP_FAIL;
    }
    if (wcsi_decode_command(WCSI_VECTOR_SET_CONFIG, WCSI_VECTOR_SET_CONFIG_LEN,
                            &header, &command) != ESP_OK || command.correlation_id != 8 ||
        command.opcode != WCSI_COMMAND_SET_CONFIG || !command.has_config ||
        memcmp(&command.config, &config, sizeof(config)) != 0 ||
        expect_vector("SET_CONFIG", WCSI_VECTOR_SET_CONFIG, WCSI_VECTOR_SET_CONFIG_LEN,
                      WCSI_VECTOR_SET_CONFIG, WCSI_VECTOR_SET_CONFIG_LEN) != ESP_OK) {
        return ESP_FAIL;
    }

    header = vector_header(7, 7000000, WCSI_MESSAGE_COMMAND_ACK);
    wcsi_ack_t ack = {
        .correlation_id = 7, .opcode = WCSI_COMMAND_GET_CONFIG,
        .status = WCSI_ACK_OK, .config = &config,
    };
    if (wcsi_encode_ack(&header, &ack, output, sizeof(output), &length) != ESP_OK ||
        expect_vector("GET_CONFIG_ACK", output, length, WCSI_VECTOR_GET_CONFIG_ACK,
                      WCSI_VECTOR_GET_CONFIG_ACK_LEN) != ESP_OK) {
        return ESP_FAIL;
    }

    uint8_t malformed[WCSI_VECTOR_GET_CONFIG_LEN];
    memcpy(malformed, WCSI_VECTOR_GET_CONFIG, sizeof(malformed));
    malformed[WCSI_HEADER_SIZE] ^= 1;
    if (wcsi_decode_command(malformed, sizeof(malformed), &header, &command) != ESP_ERR_INVALID_CRC) {
        ESP_LOGE(TAG, "corrupt CRC was accepted");
        return ESP_FAIL;
    }
    memcpy(malformed, WCSI_VECTOR_GET_CONFIG, sizeof(malformed));
    malformed[19] = 1;
    malformed[WCSI_HEADER_SIZE + 5] = 1;
    refresh_test_crc(malformed, sizeof(malformed));
    if (wcsi_decode_command(malformed, sizeof(malformed), &header, &command) != ESP_OK) {
        ESP_LOGE(TAG, "reserved storage bytes were rejected");
        return ESP_FAIL;
    }
    memcpy(malformed, WCSI_VECTOR_GET_CONFIG, sizeof(malformed));
    put_u32(malformed + 24, 9);
    refresh_test_crc(malformed, sizeof(malformed));
    if (wcsi_decode_command(malformed, sizeof(malformed), &header, &command) != ESP_ERR_INVALID_ARG) {
        ESP_LOGE(TAG, "command sequence mismatch was accepted");
        return ESP_FAIL;
    }
    memcpy(malformed, WCSI_VECTOR_GET_CONFIG, sizeof(malformed));
    put_u64(malformed + 28, 1);
    refresh_test_crc(malformed, sizeof(malformed));
    if (wcsi_decode_command(malformed, sizeof(malformed), &header, &command) != ESP_ERR_INVALID_ARG) {
        ESP_LOGE(TAG, "command device time was accepted");
        return ESP_FAIL;
    }
    memcpy(malformed, WCSI_VECTOR_GET_CONFIG, sizeof(malformed));
    malformed[WCSI_HEADER_SIZE + 4] = 0xff;
    refresh_test_crc(malformed, sizeof(malformed));
    if (wcsi_decode_command(malformed, sizeof(malformed), &header, &command) != ESP_ERR_INVALID_ARG) {
        ESP_LOGE(TAG, "unknown command opcode was accepted");
        return ESP_FAIL;
    }
    wcsi_header_t hello_header = vector_header(1, 1000000, WCSI_MESSAGE_HELLO);
    if (wcsi_encode_hello(&hello_header, &hello, output, WCSI_VECTOR_HELLO_LEN - 1,
                          &length) != ESP_ERR_INVALID_SIZE) {
        ESP_LOGE(TAG, "undersized output capacity was accepted");
        return ESP_FAIL;
    }
    wcsi_header_t csi_header = vector_header(2, 2000000, WCSI_MESSAGE_CSI_FRAME);
    csi.iq_length = 0;
    if (wcsi_encode_csi(&csi_header, &csi, output, sizeof(output), &length) != ESP_ERR_INVALID_ARG) {
        ESP_LOGE(TAG, "zero-length CSI was accepted");
        return ESP_FAIL;
    }
    header = vector_header(7, 7000000, WCSI_MESSAGE_COMMAND_ACK);
    ack.config = NULL;
    if (wcsi_encode_ack(&header, &ack, output, sizeof(output), &length) != ESP_ERR_INVALID_ARG) {
        ESP_LOGE(TAG, "successful GET_CONFIG ACK without config was accepted");
        return ESP_FAIL;
    }
    ack.status = WCSI_ACK_INVALID_ARGUMENT;
    ack.config = &config;
    if (wcsi_encode_ack(&header, &ack, output, sizeof(output), &length) != ESP_ERR_INVALID_ARG) {
        ESP_LOGE(TAG, "failed GET_CONFIG ACK with config was accepted");
        return ESP_FAIL;
    }
    ack.opcode = WCSI_COMMAND_RESET_BASELINE;
    ack.status = WCSI_ACK_OK;
    if (wcsi_encode_ack(&header, &ack, output, sizeof(output), &length) != ESP_ERR_INVALID_ARG) {
        ESP_LOGE(TAG, "RESET_BASELINE ACK with config was accepted");
        return ESP_FAIL;
    }
    return ESP_OK;
}
