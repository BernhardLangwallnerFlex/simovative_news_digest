from src.crawlers.university_domain_crawler import _clean_title


def test_strips_pipe_and_dash_sitename_kommune21():
    raw = "Beliebter Chatbot - Kommune21 online | Kommune21 - E-Government, Internet und Informationstechnik"
    assert _clean_title(raw, "www.kommune21.de") == "Beliebter Chatbot"


def test_strips_pipe_and_dash_sitename_move():
    raw = "Engere Zusammenarbeit mit dem BSI - move-online.de | move - moderne verwaltung"
    assert _clean_title(raw, "www.move-online.de") == "Engere Zusammenarbeit mit dem BSI"


def test_strips_pipe_sitename_dstgb():
    raw = "Kommunale Handlungsfähigkeit garantieren und sichern | DStGB"
    assert _clean_title(raw, "www.dstgb.de") == "Kommunale Handlungsfähigkeit garantieren und sichern"


def test_keeps_title_without_boilerplate():
    raw = "Erstsemesterbegrüßung zum Sommersemester 2025"
    assert _clean_title(raw, "www.oth-aw.de") == "Erstsemesterbegrüßung zum Sommersemester 2025"


def test_keeps_legitimate_dash_when_suffix_is_not_the_site():
    # The trailing dash-segment does not name the domain → must NOT be stripped.
    raw = "Sensorik als Brücke zwischen Forschung und Anwendung - ein Rückblick"
    assert _clean_title(raw, "www.th-ab.de") == raw


def test_empty_and_none_safe():
    assert _clean_title("", "www.kommune21.de") == ""
    assert _clean_title(None, "www.kommune21.de") is None
