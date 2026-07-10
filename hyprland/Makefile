CXX ?= g++
CXXFLAGS ?= -O2
CXXFLAGS += -shared -fPIC -std=c++23 -Wall -Wextra -Wpedantic

ifeq ($(CXX),g++)
EXTRA_FLAGS = --no-gnu-unique
endif

TARGET = same-session-target-pointer.so
SOURCE = target-pointer.cpp
INCLUDES = $(shell pkg-config --cflags pixman-1 libdrm hyprland libinput libudev wayland-server xkbcommon)

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(SOURCE)
	$(CXX) $(CXXFLAGS) $(EXTRA_FLAGS) $(INCLUDES) $< -o $@

clean:
	rm -f $(TARGET)
