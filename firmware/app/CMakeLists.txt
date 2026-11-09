# Application Zephyr de la carte UBRI v0 (nRF52840). Compiler à blanc :
#   source ~/zephyrproject/.venv/bin/activate
#   west build -b nrf52840dk/nrf52840 -d /tmp/ubri-build products/ubri/firmware/app
cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(ubri_carte)

# Le cœur testé sur PC (30 tests, parité avec trame.py) est compilé TEL QUEL, sans copie.
set(COEUR ${CMAKE_CURRENT_SOURCE_DIR}/../coeur)
target_include_directories(app PRIVATE ${COEUR})
target_sources(app PRIVATE
  src/main.c
  src/ads1299.c
  src/compteur_drdy.c
  ${COEUR}/ubri_trame.c
)
target_compile_options(app PRIVATE -Wall -Wextra -Werror)
