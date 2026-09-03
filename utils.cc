#include "utils.h"

#include <cstdlib>
#include <cstring>

using namespace v8;

char* CopyString(std::string str) {
  int len = str.length();
  char* mem = static_cast<char*>(std::malloc(len + 1));
  std::memcpy(mem, str.data(), len);
  mem[len] = 0;
  return mem;
}

char* CopyString(String::Utf8Value& value) {
  if (value.length() == 0) {
    return nullptr;
  }
  return CopyString(std::string(*value, value.length()));
}
