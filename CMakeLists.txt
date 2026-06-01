cmake_minimum_required(VERSION 3.16)
project(hft3 CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

if(MSVC)
    add_compile_options(/O2 /W4)
else()
    add_compile_options(-O3 -Wall -Wextra)
endif()

# Feature extractor library
add_library(hft_features
    packages/features_engine/cpp/src/event_context.cpp
    packages/features_engine/cpp/src/feature_extractor.cpp
    packages/features_engine/cpp/src/regime_filter.cpp
)
target_include_directories(hft_features PUBLIC
    packages/features_engine/cpp/include
)

# Risk engine
add_library(hft_risk
    risk_engine/src/risk_manager.cpp
)
target_include_directories(hft_risk PUBLIC
    risk_engine/include
)

# Decision engine
add_library(hft_decision
    packages/decision_engine/cpp/src/decision_runtime.cpp
)
target_include_directories(hft_decision PUBLIC
    packages/decision_engine/cpp/include
)
target_link_libraries(hft_decision PRIVATE hft_features)

# Rithmic gateway (C++ hot path — R|API+ wiring pending)
add_library(hft_rithmic_gateway
    rithmic_gateway/src/rithmic_adapter.cpp
)
target_include_directories(hft_rithmic_gateway PUBLIC
    rithmic_gateway/include
)

# Offline research sim (no R-API; validates C++ decision + risk + features stack)
add_executable(hft_research_sim
    tools/research_sim_main.cpp
)
target_link_libraries(hft_research_sim PRIVATE hft_features hft_risk hft_decision hft_rithmic_gateway)

add_executable(hft_rithmic_latency_probe
    scripts/latency_probe/rithmic_latency_probe.cpp
)

add_executable(hft_feature_golden
    tools/feature_golden_main.cpp
)
target_link_libraries(hft_feature_golden PRIVATE hft_features)

add_executable(hft_event_context_golden
    tools/event_context_golden_main.cpp
)
target_link_libraries(hft_event_context_golden PRIVATE hft_features)

# Copy MinGW runtime DLLs to build directory for Windows
if(WIN32 AND NOT MSVC)
    set(MINGW_DLL_DIR "" CACHE PATH "Path to MinGW bin directory containing runtime DLLs")
    if(MINGW_DLL_DIR STREQUAL "")
        if(CMAKE_CXX_COMPILER)
            get_filename_component(_COMPILER_DIR "${CMAKE_CXX_COMPILER}" DIRECTORY)
        endif()
        set(_DLL_SEARCH_PATHS
            "${_COMPILER_DIR}"
            "$ENV{MINGW64_HOME}/bin"
            "C:/msys64/mingw64/bin"
            "C:/msys64/ucrt64/bin"
        )
        foreach(_DIR ${_DLL_SEARCH_PATHS})
            if(EXISTS "${_DIR}/libgcc_s_seh-1.dll")
                set(MINGW_DLL_DIR "${_DIR}")
                break()
            endif()
        endforeach()
    endif()
    if(EXISTS "${MINGW_DLL_DIR}/libgcc_s_seh-1.dll")
        message(STATUS "Copying MinGW runtime DLLs from ${MINGW_DLL_DIR}")
        set(_DLL_OUTPUT_DIR "${CMAKE_BINARY_DIR}")
        foreach(DLL libgcc_s_seh-1.dll libstdc++-6.dll libwinpthread-1.dll)
            add_custom_command(TARGET hft_feature_golden POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different "${MINGW_DLL_DIR}/${DLL}" "${_DLL_OUTPUT_DIR}")
            add_custom_command(TARGET hft_research_sim POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different "${MINGW_DLL_DIR}/${DLL}" "${_DLL_OUTPUT_DIR}")
            add_custom_command(TARGET hft_event_context_golden POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different "${MINGW_DLL_DIR}/${DLL}" "${_DLL_OUTPUT_DIR}")
        endforeach()
    else()
        message(WARNING "MinGW runtime DLLs not found. "
                         "Set -DMINGW_DLL_DIR=<path> or ensure the DLLs are on PATH.")
    endif()
endif()
