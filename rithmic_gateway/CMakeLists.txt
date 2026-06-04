cmake_minimum_required(VERSION 3.16)

set(RAPIPLUS_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/RApiPlus/13.7.0.0")

function(configure_rapiplus_msvc_runtime target_name)
    if(MSVC)
        set_property(TARGET ${target_name} PROPERTY
            MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
    endif()
endfunction()

function(configure_hft_release_latency target_name)
    if(MSVC)
        target_compile_options(${target_name} PRIVATE
            $<$<CONFIG:Release>:/O2 /Oi /Ot /Ob3 /GL /Gw /Gy>
        )
        target_link_options(${target_name} PRIVATE
            $<$<CONFIG:Release>:/LTCG /OPT:REF /OPT:ICF>
        )
    else()
        target_compile_options(${target_name} PRIVATE
            $<$<CONFIG:Release>:-O3 -march=native -flto>
        )
        target_link_options(${target_name} PRIVATE
            $<$<CONFIG:Release>:-flto>
        )
    endif()
endfunction()

add_library(rithmic_gateway
    src/rithmic_adapter.cpp
)

target_include_directories(rithmic_gateway PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${RAPIPLUS_ROOT}/include
)

target_compile_features(rithmic_gateway PUBLIC cxx_std_17)
configure_rapiplus_msvc_runtime(rithmic_gateway)
configure_hft_release_latency(rithmic_gateway)

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
        ws2_32 crypt32 iphlpapi
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

add_library(rithmic_gateway_shared SHARED
    src/c_api.cpp
    src/rithmic_adapter.cpp
)

target_include_directories(rithmic_gateway_shared PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${RAPIPLUS_ROOT}/include
)
target_compile_definitions(rithmic_gateway_shared PRIVATE HFT_RITHMIC_C_API_EXPORTS)

target_compile_features(rithmic_gateway_shared PUBLIC cxx_std_17)
configure_rapiplus_msvc_runtime(rithmic_gateway_shared)

set_target_properties(rithmic_gateway_shared PROPERTIES
    POSITION_INDEPENDENT_CODE ON
)

if(NOT MSVC)
    target_compile_options(rithmic_gateway_shared PRIVATE
        -Wall -Wextra -Wno-sign-compare -Wno-write-strings
        -Wpointer-arith -Winline -Wno-deprecated
        -fno-strict-aliasing
    )
    target_compile_definitions(rithmic_gateway_shared PRIVATE
        LINUX _REENTRANT
    )
else()
    target_compile_options(rithmic_gateway_shared PRIVATE /O2 /W4)
endif()

if(WIN32)
    target_link_libraries(rithmic_gateway_shared PUBLIC
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
        ws2_32 crypt32 iphlpapi
    )
else()
    target_link_libraries(rithmic_gateway_shared PRIVATE
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

add_library(hft_rithmic_gateway_shared ALIAS rithmic_gateway_shared)

add_executable(rithmic_latency_probe
    tools/rithmic_latency_probe.cpp
)
target_include_directories(rithmic_latency_probe PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${RAPIPLUS_ROOT}/include
)
target_link_libraries(rithmic_latency_probe PRIVATE rithmic_gateway)
target_compile_features(rithmic_latency_probe PRIVATE cxx_std_17)
configure_rapiplus_msvc_runtime(rithmic_latency_probe)
configure_hft_release_latency(rithmic_latency_probe)
if(NOT MSVC)
    target_compile_options(rithmic_latency_probe PRIVATE
        -Wall -Wextra -Wno-sign-compare -Wno-write-strings
        -Wpointer-arith -Winline -Wno-deprecated
        -fno-strict-aliasing
    )
    target_compile_definitions(rithmic_latency_probe PRIVATE LINUX _REENTRANT)
endif()
