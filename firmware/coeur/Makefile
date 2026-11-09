# Cœur du microprogramme UBRI : tests sur PC (gcc). Le même ubri_trame.c sera compilé pour le nRF52840 (Zephyr).
CFLAGS += -std=c99 -O2 -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Werror -fsanitize=address,undefined
BUILD ?= ./build

.PHONY: test parite clean
$(BUILD)/test_coeur: ubri_trame.c ubri_trame.h tests/test_coeur.c
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -o $@ ubri_trame.c tests/test_coeur.c

test: $(BUILD)/test_coeur
	$(BUILD)/test_coeur

parite: $(BUILD)/test_coeur
	python3 -B tests/parite.py $(BUILD)/test_coeur

clean:
	test -n "$(BUILD)" && test "$(BUILD)" != "." && test "$(BUILD)" != ".." && rm -rf -- "$(BUILD)"
