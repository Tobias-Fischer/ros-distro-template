// This file is generated from ros-distro-template (template/tests/ros2-std-msgs/cpp_consumer/main.cpp).
// If you change it here, upstream the change: comment `@robostack-bot upstream-to-template` on your PR.

#include <iostream>

#include <std_msgs/msg/string.hpp>

int main()
{
  std_msgs::msg::String msg;
  msg.data = "hello";
  std::cout << "std_msgs C++ consumer started fine: " << msg.data << std::endl;
  return 0;
}
