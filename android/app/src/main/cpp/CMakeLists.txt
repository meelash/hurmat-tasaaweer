cmake_minimum_required(VERSION 3.22)
project(hurmat)

set(ncnn_DIR ${CMAKE_SOURCE_DIR}/../../../../third_party/ncnn/${ANDROID_ABI}/lib/cmake/ncnn)
find_package(ncnn REQUIRED)

add_library(hurmat SHARED hurmat_jni.cpp)
target_link_libraries(hurmat ncnn android jnigraphics log)
