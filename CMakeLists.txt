cmake_minimum_required(VERSION 3.16)
project(hft3 CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

add_compile_options(-O3 -Wall -Wextra)

# Feature extractor library
add_library(hft_features
    features_engine/cpp/src/event_context.cpp
    features_engine/cpp/src/feature_extractor.cpp
    features_engine/cpp/src/regime_filter.cpp
)
target_include_directories(hft_features PUBLIC
    features_engine/cpp/include
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
    decision_engine/cpp/src/decision_runtime.cpp
)
target_include_directories(hft_decision PUBLIC
    decision_engine/cpp/include
)
target_link_libraries(hft_decision PRIVATE hft_features)

# Offline research sim stub (no R-API)
add_executable(hft_research_sim
    tools/research_sim_main.cpp
)
target_link_libraries(hft_research_sim PRIVATE hft_features hft_risk hft_decision)

add_executable(hft_feature_golden
    tools/feature_golden_main.cpp
)
target_link_libraries(hft_feature_golden PRIVATE hft_features)
