"""De CI-proef meldt de startweigering van de laatste poging en houdt vrije logtekst privé."""

import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

from allinone_container import Trial


class Containerstatus(unittest.TestCase):
    def test_weigering_gebruikt_alleen_de_huidige_start(self):
        begin = '2026-01-02T03:04:05.000000000Z'
        categorie = 'Deze opslag is in gebruik door een andere app-instantie'
        log = f'Opstarten geweigerd: {categorie}. Interne proeftoelichting.'.encode()

        def antwoord(*args):
            if args[0] == 'inspect':
                waarde = begin.encode() if 'StartedAt' in args[2] else b'exited'
            elif args == ('logs', '--since', begin, 'proef'):
                waarde = log
            else:
                waarde = b'Database-update geweigerd\n' + log
            return CompletedProcess(args, 0, stdout=waarde, stderr=b'')

        with patch('allinone_container.docker', side_effect=antwoord), self.assertRaises(AssertionError) as fout:
            Trial('proef', None).healthy('proef')
        self.assertEqual(str(fout.exception), f'proef: containerstatus exited ({categorie})')
