cmake_minimum_required(VERSION 3.16)

set(RAPIPLUS_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/RApiPlus/13.7.0.0")

add_library(rithmic_gateway
    src/rithmic_adapter.cpp
)

target_include_directories(rithmic_gateway PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${RAPIPLUS_ROOT}/include
)

target_compile_features(rithmic_gateway PUBLIC cxx_std_17)

if(NOT MSVC)
    target_compile_options(rithmic_gateway PRIVATE
        -Wall -Wextra -Wno-sign-compare -Wno-write-strings
        -Wpointer-arith -Winline -Wno-deprecated
        -fno-strict-aliasing
    )
    target_compile_definitions(rithmic_gateway PRIVATE
        LINUX _REENTRANT
    )
else()
    target_compile_options(rithmic_gateway PRIVATE /O2 /W4)
endif()

# Platform-specific RApiPlus library linking
if(WIN32)
    set(RAPIPLUS_LIB_DIR "${RAPIPLUS_ROOT}/win10/lib")

    target_link_libraries(rithmic_gateway PUBLIC
        ${RAPIPLUS_LIB_DIR}/RApiPlus_mt64.lib
        ${RAPIPLUS_LIB_DIR}/OmneStreamEngine_mt64.lib
        ${RAPIPLUS_LIB_DIR}/OmneChannel_mt64.lib
        ${RAPIPLUS_LIB_DIR}/OmneEngine_mt64.lib
        ${RAPIPLUS_LIB_DIR}/api_mt64.lib
        ${RAPIPLUS_LIB_DIR}/apistb_mt64.lib
        ${RAPIPLUS_LIB_DIR}/kit_mt64.lib
        ${RAPIPLUS_LIB_DIR}/libssl_mt64.lib
        ${RAPIPLUS_LIB_DIR}/libcrypto_mt64.lib
        ${RAPIPLUS_LIB_DIR}/zlib_mt64.lib
        ws2_32 crypt32
    )
else()
    set(RAPIPLUS_LIB_DIR "${RAPIPLUS_ROOT}/linux-gnu-4.18-x86_64/lib")

    target_link_libraries(rithmic_gateway PRIVATE
        ${RAPIPLUS_LIB_DIR}/libRApiPlus-optimize.a
        ${RAPIPLUS_LIB_DIR}/libOmneStreamEngine-optimize.a
        ${RAPIPLUS_LIB_DIR}/libOmneChannel-optimize.a
        ${RAPIPLUS_LIB_DIR}/libOmneEngine-optimize.a
        ${RAPIPLUS_LIB_DIR}/lib_api-optimize.a
        ${RAPIPLUS_LIB_DIR}/lib_apipoll-stubs-optimize.a
        ${RAPIPLUS_LIB_DIR}/lib_kit-optimize.a
        ${RAPIPLUS_LIB_DIR}/libssl.a
        ${RAPIPLUS_LIB_DIR}/libcrypto.a
        z pthread rt dl
    )
endif()

add_library(hft_rithmic_gateway ALIAS rithmic_gateway)