cmake_minimum_required(VERSION 3.13)

# Board header firmware/boards/probepen_ai.h: RP2354B (80-pin, 48 GPIOs), 2 MB stacked flash, USB stdio.
set(PICO_BOARD_HEADER_DIRS ${CMAKE_CURRENT_LIST_DIR}/boards)
if(NOT PICO_BOARD)
    set(PICO_BOARD probepen_ai CACHE STRING "Board header in firmware/boards")
endif()

# The SDK from -DPICO_SDK_PATH=... or the PICO_SDK_PATH environment variable; without it only the
# host-testable library builds.
if(NOT PICO_SDK_PATH AND DEFINED ENV{PICO_SDK_PATH})
    set(PICO_SDK_PATH $ENV{PICO_SDK_PATH})
endif()
if(PICO_SDK_PATH)
    include(${PICO_SDK_PATH}/external/pico_sdk_import.cmake)
else()
    message(STATUS "PICO_SDK_PATH not set: building the host library only, no probepen_fw")
endif()

project(probepen_ai C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

add_library(probepen_core STATIC
    src/afe_cal.c
    src/scope_capture.c
    src/logic_probe.c
    src/lcd.c
    src/app.c
    src/calstore.c
    src/usb_proto.c
    src/stream.c
    src/deep.c
)
target_include_directories(probepen_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_options(probepen_core PRIVATE -Wall -Wextra -Werror)

if(COMMAND pico_sdk_init)
    pico_sdk_init()

    add_executable(probepen_fw src/main.c)
    pico_generate_pio_header(probepen_fw ${CMAKE_CURRENT_LIST_DIR}/ad9288_capture.pio)
    pico_generate_pio_header(probepen_fw ${CMAKE_CURRENT_LIST_DIR}/pod_capture.pio)
    target_link_libraries(probepen_fw
        probepen_core
        pico_stdlib
        hardware_pio
        hardware_dma
        hardware_spi
        hardware_adc
        hardware_flash
        hardware_sync
    )
    # USB serial transmit: TinyUSB's default is a 64-byte buffer (one packet per write). Larger buffers let
    # one write carry many packets, which streaming needs.
    target_compile_definitions(probepen_fw PRIVATE
        CFG_TUD_CDC_TX_BUFSIZE=4096
        CFG_TUD_CDC_EP_BUFSIZE=512
        CFG_TUD_CDC_RX_BUFSIZE=256
    )
    pico_enable_stdio_usb(probepen_fw 1)
    pico_enable_stdio_uart(probepen_fw 0)
    pico_add_extra_outputs(probepen_fw)
endif()
