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
