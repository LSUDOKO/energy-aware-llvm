cmake_minimum_required(VERSION 3.13)
project(EnergyAwareLLVM)

# Locate LLVM
# find_package(LLVM REQUIRED CONFIG)
# include_directories(${LLVM_INCLUDE_DIRS})
# add_definitions(${LLVM_DEFINITIONS})

set(CMAKE_CXX_STANDARD 17)

# Skeleton targets
add_library(IRMetricsExtractor MODULE src/Passes/IRMetricsExtractorPass.cpp)
# target_link_libraries(IRMetricsExtractor ${LLVM_LIBRARIES})
