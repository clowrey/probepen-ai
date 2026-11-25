cmake_minimum_required(VERSION 3.13)

if(NOT PICO_BOARD)
    set(PICO_BOARD pico2 CACHE STRING "Target RP2350/RP2354B board")
endif()

if(DEFINED ENV{PICO_SDK_PATH})
    include($ENV{PICO_SDK_PATH}/external/pico_sdk_import.cmake)
endif()

project(probepen_ai C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

add_library(probepen_core STATIC
    src/afe_cal.c
    src/scope_capture.c
    src/logic_probe.c
    src/lcd_st7735.c
    src/usb_proto.c
)
target_include_directories(probepen_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_options(probepen_core PRIVATE -Wall -Wextra -Werror)

if(COMMAND pico_sdk_init)
    pico_sdk_init()

    add_executable(probepen_fw src/main.c)
    pico_generate_pio_header(probepen_fw ${CMAKE_CURRENT_LIST_DIR}/ms9280_capture.pio)
    pico_generate_pio_header(probepen_fw ${CMAKE_CURRENT_LIST_DIR}/pod_capture.pio)
    target_link_libraries(probepen_fw
        probepen_core
        pico_stdlib
        hardware_pio
        hardware_dma
        hardware_spi
        hardware_pwm
        hardware_adc
    )
    pico_enable_stdio_usb(probepen_fw 1)
    pico_enable_stdio_uart(probepen_fw 0)
    pico_add_extra_outputs(probepen_fw)
endif()
