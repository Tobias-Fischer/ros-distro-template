# This file is generated from ros-distro-template (template/tests/ros2-std-msgs/cpp_consumer/CMakeLists.txt).
# If you change it here, upstream the change: comment `@robostack-bot upstream-to-template` on your PR.

cmake_minimum_required(VERSION 3.20)
project(std_msgs_cpp_consumer CXX)

find_package(std_msgs REQUIRED)

add_executable(std_msgs_cpp_consumer main.cpp)
target_link_libraries(std_msgs_cpp_consumer ${std_msgs_TARGETS})
