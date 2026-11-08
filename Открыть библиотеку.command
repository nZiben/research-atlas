#!/bin/zsh
cd "${0:A:h}" || exit 1
if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python -m review_forge serve --open
fi
exec python3 -m review_forge serve --open
