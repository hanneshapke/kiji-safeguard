# Render the kiji-safeguard demo GIFs.
#
#   make            # render every tape into demo/gif/
#   make hero       # just the headline rug-pull GIF
#   make clean      # remove rendered GIFs and the demo venv
#
# Real work lives in render.sh so CI and humans run the exact same thing.

.PHONY: all hero magic cli clean

all:
	./render.sh

hero:
	./render.sh 01-rug-pull

magic:
	./render.sh 02-magic-import

cli:
	./render.sh 03-cli-tour

clean:
	rm -f gif/*.gif
	rm -rf ../.venv-demo
