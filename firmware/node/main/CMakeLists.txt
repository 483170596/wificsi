idf_component_register(
    SRCS "app_main.c" "wcsi_node.c" "wcsi_protocol.c"
    INCLUDE_DIRS "."
    REQUIRES esp_hw_support esp_wifi_sensing
)
