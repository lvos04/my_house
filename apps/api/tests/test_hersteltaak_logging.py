"""De toegangscode voor een taakrapport mag nergens in een logregel terechtkomen."""

import io
import logging
import secrets
import sys

import pytest
from uvicorn.logging import AccessFormatter, DefaultFormatter

from shc_api.logfilter import DbDetailFilter, uvicorn_log_config


@pytest.mark.parametrize("status", [200, 404, 500])
def test_toegangslog_behoudt_methode_en_status(status):
    config = uvicorn_log_config()
    uitvoer = io.StringIO()
    handler = logging.StreamHandler(uitvoer)
    formatter = config["formatters"]["access"]
    handler.setFormatter(AccessFormatter(fmt=formatter["fmt"], use_colors=False))
    handler.addFilter(DbDetailFilter())
    code = secrets.token_urlsafe(24)
    pad = f"/api/v1/system/restore/jobs/{code}?herhaling={code}"
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("proef", "GET", pad, "1.1", status), None)
    handler.handle(record)
    if f'"GET /api/v1/system/restore/jobs/{{job_id}} HTTP/1.1" {status}' not in uitvoer.getvalue():
        pytest.fail("De toegangsregel mist het routesjabloon of de HTTP-status.")
    if code in uitvoer.getvalue() or code in str(record.args):
        pytest.fail("Een toegangsregel bevat de proefcode.")


def test_eigen_foutlog_schermt_bericht_exceptie_en_stack_af():
    code = secrets.token_urlsafe(24)
    pad = "/system/restore/jobs/" + code
    try:
        raise ValueError(pad)
    except ValueError:
        record = logging.LogRecord("shc_api.proef", logging.ERROR, __file__, 1,
                                   "Taakrapport %s gaf een fout", (pad,), sys.exc_info(), sinfo="Stack: " + pad)
    DbDetailFilter().filter(record)
    tekst = logging.Formatter().format(record)
    if tekst.count("/system/restore/jobs/{job_id}") != 3 or code in tekst:
        pytest.fail("Bericht, exceptie of stack bevat geen volledig afgeschermd taakpad.")


def test_terminalbericht_en_eerder_opgemaakte_exceptie():
    code = secrets.token_urlsafe(24)
    pad = "/system/restore/jobs/" + code
    record = logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1, "Taakrapport", (), None)
    record.color_message = "Taakrapport " + pad
    record.exc_text = "Onbereikbaar " + pad
    DbDetailFilter().filter(record)
    tekst = DefaultFormatter("%(message)s", use_colors=True).format(record)
    if tekst.count("/system/restore/jobs/{job_id}") != 2 or code in tekst:
        pytest.fail("Het terminalbericht of de eerder opgemaakte exceptie bevat een onafgeschermd taakpad.")


def test_foutmelding_met_taakveld_en_pollpad():
    code = secrets.token_urlsafe(24)
    taak = {"job_id": code, "poll": "/api/v1/system/restore/jobs/" + code}
    record = logging.LogRecord("shc_api.proef", logging.WARNING, __file__, 1, "Taakrapport %s", (taak,), None)
    DbDetailFilter().filter(record)
    if code in logging.Formatter().format(record):
        pytest.fail("De foutmelding bevat de waarde van het taakveld of het pollpad.")
