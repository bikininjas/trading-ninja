"""Le préparateur d'environnement conserve les secrets et refuse une cible réelle."""
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'deploy/enable-multiple-positions-wsl.sh'
CONFIGURE = SCRIPT.read_text().split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]


def test_installer_configuration_preserves_other_values_and_private_mode(tmp_path):
    target = tmp_path / 'engine.env'
    target.write_text('DATABASE_URL=private-example\nTRADING_MODE=paper\nTRADING_SYMBOL=BTC/EUR\n'
                      'TRADING_SYMBOLS=BTC/EUR,ETH/EUR\nMAX_OPEN_POSITIONS=1\nRISK_PER_TRADE_PCT=0.03\n')
    target.chmod(0o600)
    result = subprocess.run([sys.executable, '-', str(target)], input=CONFIGURE,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    text = target.read_text()
    assert 'DATABASE_URL=private-example' in text and 'RISK_PER_TRADE_PCT=0.03' in text
    assert text.count('MAX_OPEN_POSITIONS=') == 1 and 'MAX_OPEN_POSITIONS=5' in text
    assert 'TRADING_SYMBOLS=BTC/EUR,ETH/EUR,SOL/EUR,LINK/EUR,AAVE/EUR,SUI/EUR,NEAR/EUR' in text
    assert target.stat().st_mode & 0o777 == 0o600
    assert not result.stdout and not result.stderr


def test_installer_refuses_live_before_changing_the_file(tmp_path):
    target = tmp_path / 'engine.env'
    text = 'TRADING_MODE=live\nMAX_OPEN_POSITIONS=1\n'
    target.write_text(text)
    result = subprocess.run([sys.executable, '-', str(target)], input=CONFIGURE,
                            text=True, capture_output=True)
    assert result.returncode != 0
    assert target.read_text() == text


@pytest.mark.parametrize('mode', ['paper', 'live'])
def test_configuration_preflight_never_writes_private_values(tmp_path, mode):
    target = tmp_path / 'local.env'
    text = f'TRADING_MODE={mode}\nDATABASE_URL=private-example\nMAX_OPEN_POSITIONS=1\n'
    target.write_text(text)
    result = subprocess.run([sys.executable, '-', str(target), '--check'], input=CONFIGURE,
                            text=True, capture_output=True)
    assert (result.returncode == 0) == (mode == 'paper')
    assert target.read_text() == text
    assert 'private-example' not in result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == [target]


def test_configuration_parser_handles_spaces_quotes_and_comments(tmp_path):
    target = tmp_path / 'local.env'
    target.write_text('# TRADING_MODE=live\nexport TRADING_MODE = "paper"\n'
                      'MAX_OPEN_POSITIONS = 1\nexport MAX_OPEN_POSITIONS=2\n'
                      'TRADING_SYMBOLS = "BTC/EUR, SOL/EUR"\n')
    result = subprocess.run([sys.executable, '-', str(target)], input=CONFIGURE,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    text = target.read_text()
    assert text.count('MAX_OPEN_POSITIONS') == 1
    assert text.count('SOL/EUR') == 1
    assert '# TRADING_MODE=live' in text
