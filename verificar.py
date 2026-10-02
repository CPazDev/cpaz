"""Executa os testes isolando a configuração do banco de produção."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import database as db


if __name__ == '__main__':
    suite=unittest.defaultTestLoader.discover(str(Path(__file__).parent/'tests'))
    with patch.object(db,'remote_settings',return_value=''):
        result=unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
