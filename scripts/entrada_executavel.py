"""Ponto de entrada do PyInstaller: o mesmo `meu-cofre` do pyproject."""

import sys

from meu_cofre.ui.app import main

sys.exit(main())
