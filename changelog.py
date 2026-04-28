# changelog.py - Historia wersji aplikacji. Najnowsza wersja na gorze listy.

CHANGELOG = [
    {
        "version": "1.1.1",
        "date": "2026-04-28",
        "entries": [
            ("Formularz", [
                "Nowy przycisk 'Flota' - otwiera stronę floty bezpośrednio z formularza",
                "Nowy przycisk 'Wklej z JSON' - wczytuje dane formularza z JSON (tylko nowy wpis)",
            ]),
            ("Lista główna (prawy klik)", [
                "Opcja 'Przejdź do floty' przy zaznaczeniu jednego wiersza",
                "'Kopiuj do dyżurów' nie pojawia się gdy moduł Dyżurny jest wyłączony",
            ]),
            ("Ustawienia", [
                "Nowy słownik 'Linki flot' - przypisz URL do każdej floty; importowany z arkusza 'Linki' w pliku Excel",
                "Checkbox Dyżurny automatycznie włącza/wyłącza podświetlenie wierszy dyżurowych",
                "Odświeżony układ zakładek",
                "Nowa zakładka 'O aplikacji' - historia ostatnich wersji",
            ]),
            ("Import", [
                "Przy imporcie z Excela pole Model urządzenia wykrywany automatycznie na podstawie ID",
            ]),
            ("Inne", [
                "Przy pierwszym uruchomieniu po aktualizacji wyświetlany jest dialog z informacją o zmianach",
            ]),
        ],
    },
]
