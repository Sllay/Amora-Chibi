cmake_minimum_required(VERSION 3.22.1)
project(amora_voice_native LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(WORLD_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/third_party/world")
if(NOT EXISTS "${WORLD_ROOT}/src/dio.cpp")
    message(FATAL_ERROR "WORLD source missing. Run android/tools/prepare-deps.sh first.")
endif()

add_library(world STATIC
    ${WORLD_ROOT}/src/cheaptrick.cpp
    ${WORLD_ROOT}/src/codec.cpp
    ${WORLD_ROOT}/src/common.cpp
    ${WORLD_ROOT}/src/d4c.cpp
    ${WORLD_ROOT}/src/dio.cpp
    ${WORLD_ROOT}/src/fft.cpp
    ${WORLD_ROOT}/src/harvest.cpp
    ${WORLD_ROOT}/src/matlabfunctions.cpp
    ${WORLD_ROOT}/src/stonemask.cpp
    ${WORLD_ROOT}/src/synthesis.cpp
    ${WORLD_ROOT}/src/synthesisrealtime.cpp
)
target_include_directories(world PUBLIC "${WORLD_ROOT}/src")
set_target_properties(world PROPERTIES POSITION_INDEPENDENT_CODE ON)

add_library(
    amora_voice_native
    SHARED
    amora_voice_jni.cpp
    amora_voice_processor.cpp
)
target_include_directories(amora_voice_native PRIVATE "${WORLD_ROOT}/src" "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(amora_voice_native PRIVATE world log)
