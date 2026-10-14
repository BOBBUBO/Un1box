cmake_minimum_required(VERSION 3.22)
project(LightTouchPortable VERSION 0.1 LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(ZLIB REQUIRED)
find_package(Threads REQUIRED)
set(LIGHTTOUCH_QEMU_SOURCE "" CACHE PATH "qemu-ios source providing versioned portable headers")
if(NOT EXISTS "${LIGHTTOUCH_QEMU_SOURCE}/contrib/ios-app/qemu-ios-ui.h")
 message(FATAL_ERROR "Set LIGHTTOUCH_QEMU_SOURCE to the portable qemu-ios source")
endif()
add_library(lighttouch_runtime STATIC src/Storage.cpp src/Runtime.cpp src/GuestPackage.cpp src/Library.cpp src/Screenshot.cpp)
set_target_properties(lighttouch_runtime PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(lighttouch_runtime PUBLIC include PRIVATE "${LIGHTTOUCH_QEMU_SOURCE}/contrib/ios-app" "${LIGHTTOUCH_QEMU_SOURCE}/contrib/macos-app")
target_link_libraries(lighttouch_runtime PUBLIC ZLIB::ZLIB Threads::Threads ${CMAKE_DL_LIBS})
target_compile_options(lighttouch_runtime PRIVATE -Wall -Wextra -Werror)
# Full prepared-base integrity reads also run in diagnostic builds. Optimize
# both translation units that emit inline Sha256 methods, and their contract
# test, consistently so weak/COMDAT selection cannot mix optimized and baseline
# copies. Preserve assertions, hardening/debug flags and release build policies.
# Clang's MSVC frontend and AppleClang deliberately retain their existing flags.
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU" OR
   (CMAKE_CXX_COMPILER_ID STREQUAL "Clang" AND
    CMAKE_CXX_COMPILER_FRONTEND_VARIANT STREQUAL "GNU"))
 set(lighttouch_sha_debug_optimization "$<$<OR:$<CONFIG:Debug>,$<STREQUAL:$<CONFIG>,>>:-O2>")
 set_property(SOURCE src/Storage.cpp src/GuestPackage.cpp tests/Contracts.cpp
              APPEND PROPERTY COMPILE_OPTIONS "${lighttouch_sha_debug_optimization}")
endif()
option(LIGHTTOUCH_ENABLE_DEBUG_QMP "Explicit private diagnostic QMP hook; never enabled in release" OFF)
if(LIGHTTOUCH_ENABLE_DEBUG_QMP)
 target_compile_definitions(lighttouch_runtime PRIVATE LIGHTTOUCH_ENABLE_DEBUG_QMP=1)
endif()
option(LIGHTTOUCH_BUILD_TESTS "Synthetic ABI/storage regression tests; no guest claims" OFF)
if(LIGHTTOUCH_BUILD_TESTS)
 enable_testing()
 add_executable(lighttouch_contract_test tests/Contracts.cpp)
 target_link_libraries(lighttouch_contract_test PRIVATE lighttouch_runtime)
 target_compile_options(lighttouch_contract_test PRIVATE -UNDEBUG)
 add_test(NAME lighttouch_contract COMMAND lighttouch_contract_test)
 # Public C ABI only: do not emit test-local inline SHA methods that could
 # replace the archive's implementation during this integrity regression.
 add_executable(lighttouch_file_hash_test tests/FileHash.cpp)
 target_link_libraries(lighttouch_file_hash_test PRIVATE lighttouch_runtime)
 target_compile_options(lighttouch_file_hash_test PRIVATE -UNDEBUG)
 add_test(NAME lighttouch_file_hash COMMAND lighttouch_file_hash_test)
 add_executable(lighttouch_observed_admission_test tests/ObservedAdmission.cpp)
 target_link_libraries(lighttouch_observed_admission_test PRIVATE lighttouch_runtime)
 target_compile_options(lighttouch_observed_admission_test PRIVATE -UNDEBUG)
 add_test(NAME lighttouch_observed_admission COMMAND lighttouch_observed_admission_test)
 add_executable(lighttouch_pcm_epoch_test tests/PcmEpoch.cpp)
 target_link_libraries(lighttouch_pcm_epoch_test PRIVATE Threads::Threads)
 target_compile_options(lighttouch_pcm_epoch_test PRIVATE -Wall -Wextra -Werror -UNDEBUG)
 add_test(NAME lighttouch_pcm_epoch COMMAND lighttouch_pcm_epoch_test)
endif()
