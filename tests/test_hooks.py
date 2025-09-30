
import pytest
from hooks import get_daypart_greeting, is_greeting, format_main_menu, RED_FLAG_TERMS

def test_is_greeting_basic():
    assert is_greeting("Hola")
    assert is_greeting("buenos dias")
    assert is_greeting("Buenas tardes")
    assert not is_greeting("Quiero agendar una cita")

def test_get_daypart_greeting():
    assert get_daypart_greeting(6) == "Buenos días"
    assert get_daypart_greeting(12) == "Buenos días"
    assert get_daypart_greeting(15) == "Buenas tardes"
    assert get_daypart_greeting(21) == "Buenas noches"

def test_format_main_menu_options():
    menu = [
        {"key": "1", "label": "Agendar cita"},
        {"key": "2", "label": "Ver ubicaciones"},
        {"key": "3", "label": "Precios"},
        {"key": "4", "label": "Ubicaciones"},
        {"key": "5", "label": "Reagendar o cancelar"},
        {"key": "6", "label": "Atrás"},
        {"key": "9", "label": "Inicio"},
    ]
    formatted = format_main_menu(menu)
    for k in ["1", "2", "3", "4", "5", "6", "9"]:
        assert k in formatted

def test_red_flag_terms_keywords():
    keywords = ["quemazon", "hormigueo", "descargas", "dolor", "frialdad", "calentura", "fiebre", "herida"]
    found = [k for k in keywords if any(k in t for t in RED_FLAG_TERMS)]
    assert set(found) == set(keywords)
