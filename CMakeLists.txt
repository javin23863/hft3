cmake_minimum_required(VERSION 3.16)

if(POLICY CMP0091)
    cmake_policy(SET CMP0091 NEW)
endif()

# Allow caller to pass Python interpreter path (needed on Windows where bare
# "python" may stall due to site-hook issues).
if(DEFINED ENV{HFT3_PYTHON_EXECUTABLE})
    set(Python_EXECUTABLE "$ENV{HFT3_PYTHON_EXECUTABLE}" CACHE FILEPATH "Python interpreter" FORCE)
elseif(DEFINED HFT3_PYTHON_EXECUTABLE)
    set(Python_EXECUTABLE "${HFT3_PYTHON_EXECUTABLE}" CACHE FILEPATH "Python interpreter" FORCE)
endif()

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

# Rithmic gateway (C++ hot path — RApiPlus SDK integration)
add_subdirectory(rithmic_gateway)

# Offline research sim (no R-API; validates C++ decision + risk + features stack)
add_executable(hft_research_sim
    tools/research_sim_main.cpp
)
target_link_libraries(hft_research_sim PRIVATE hft_features hft_risk hft_decision hft_rithmic_gateway)

add_custom_target(hft_rithmic_latency_probe DEPENDS rithmic_latency_probe)

add_executable(hft_feature_golden
    tools/feature_golden_main.cpp
)
target_link_libraries(hft_feature_golden PRIVATE hft_features)

add_executable(hft_event_context_golden
    tools/event_context_golden_main.cpp
)
target_link_libraries(hft_event_context_golden PRIVATE hft_features)

# ── pybind11 Python extension ────────────────────────────────────────────────
# Locate pybind11 via the cmake directory reported by:
#   python -S -m pybind11 --cmakedir
# Pass it with -Dpybind11_DIR=<path> or set HFT3_PYBIND11_DIR env var.
if(DEFINED ENV{HFT3_PYBIND11_DIR})
    set(pybind11_DIR "$ENV{HFT3_PYBIND11_DIR}" CACHE PATH "pybind11 cmake dir")
endif()

find_package(pybind11 CONFIG QUIET)

if(pybind11_FOUND)
    message(STATUS "pybind11 found: ${pybind11_VERSION} at ${pybind11_DIR}")

    # Python extension module — same source as hft_features, same compilation flags.
    pybind11_add_module(hft3_features_cpp
        packages/features_engine/cpp/bindings/py_features.cpp
        packages/features_engine/cpp/src/event_context.cpp
        packages/features_engine/cpp/src/feature_extractor.cpp
        packages/features_engine/cpp/src/regime_filter.cpp
    )
    target_include_directories(hft3_features_cpp PRIVATE
        packages/features_engine/cpp/include
    )
    if(MSVC)
        target_compile_options(hft3_features_cpp PRIVATE /O2 /W4)
    else()
        target_compile_options(hft3_features_cpp PRIVATE -O3 -Wall -Wextra)
    endif()

    # Place the .pyd/.so directly in build/ so the loader can find it.
    set_target_properties(hft3_features_cpp PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
    )
    # Also copy to Release/ sub-dir if that's where the other binaries land.
    if(WIN32 AND NOT MSVC)
        set(_PYBIND_OUT "${CMAKE_BINARY_DIR}")
    endif()

    message(STATUS "hft3_features_cpp target configured; output -> ${CMAKE_BINARY_DIR}")
else()
    message(WARNING
        "pybind11 not found — hft3_features_cpp will NOT be built.\n"
        "Install pybind11 and pass -Dpybind11_DIR=<path> (from: python -S -m pybind11 --cmakedir)")
endif()

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
            if(TARGET hft3_features_cpp)
                add_custom_command(TARGET hft3_features_cpp POST_BUILD
                    COMMAND ${CMAKE_COMMAND} -E copy_if_different "${MINGW_DLL_DIR}/${DLL}" "${_DLL_OUTPUT_DIR}")
            endif()
        endforeach()
    else()
        message(WARNING "MinGW runtime DLLs not found. "
                         "Set -DMINGW_DLL_DIR=<path> or ensure the DLLs are on PATH.")
    endif()
endif()
