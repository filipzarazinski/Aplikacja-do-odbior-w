# changelog.py - Historia wersji aplikacji. Najnowsza wersja na gorze listy.

CHANGELOG = [
    {
        "version": "1.1.4",
        "date": "2026-04-29",
        "entries": [
            ("Lista główna", [
                "Przytrzymanie środkowego przycisku myszy (scrolla) i ruch myszką przewija tabelę w dowolnym kierunku – w lewo, prawo, górę i dół",
            ]),
        ],
    },
    {
        "version": "1.1.3",
        "date": "2026-04-29",
        "entries": [
            ("Lista główna", [
                "Próba otwarcia formularza już otwartego rekordu przenosi istniejące okno na wierzch zamiast otwierac duplikat",
                "Tabela odswiezana po zapisie, duplikowaniu i usuwaniu z zachowaniem aktywnych filtrow (wyszukiwarka + daty)",
                "Przycisk Duplikuj jest nieaktywny po odswiezeniu tabeli gdy zaden wiersz nie jest zaznaczony",
            ]),
            ("Formularz", [
                "Nowy typ: Demontaz - widoczny miedzy Serwisem a Telefonem",
                "Pole 'Przekladka z' ma teraz przycisk kopiowania do schowka tak jak Nr rej., ID i SIM",
            ]),
            ("Ustawienia", [
                "Nowe opcje w Ogolne: 'Pamietaj filtr wyszukiwarki' i 'Pamietaj filtry dat' - przy wlaczeniu filtry sa przywracane po ponownym uruchomieniu aplikacji",
                "Po aktualizacji aplikacji zapisane filtry sa automatycznie resetowane",
                "Zamkniecie ustawien odswiezа tabele z zachowaniem aktywnych filtrow",
            ]),
        ],
    },
    {
        "version": "1.1.2",
        "date": "2026-04-28",
        "entries": [
            ("Formularz", [
                "Duplikuj nie kopiuje już numeru bocznego, modelu urządzenia, numeru tabletu ani numerów seryjnych zabezpieczeń wlewu paliwa",
                "Po duplikowaniu z formularza tabela odświeża się automatycznie",
            ]),
            ("Wyszukiwarka", [
                "Wprowadzono rozbudowane modyfikacje silnika wyszukiwania - przycisk SZUKAJ dziala, dodano ikone (i) z podpowiedzia skladni.",
                "AND (srednik): 'Transport ABC;typ:Montaz' - fraza z spacjami i filtr kolumnowy jednoczesnie",
                "LUB w kolumnie (przecinek): 'typ:Montaz,Serwis' - typ Montaz lub Serwis",
                "LUB miedzy kolumnami (przecinek): 'typ:Montaz,firma:ACME' - typ Montaz lub firma ACME",
                "Negacja: '!Telefon' wyklucza globalnie, 'typ:!Telefon' wyklucza w kolumnie typ",
            ]),
            ("O aplikacji", [
                "Historia wersji wyświetlana jako zwijana lista - kliknij wersję żeby zobaczyć zmiany",
                "Przy aktualizacji przez kilka wersji jednocześnie widoczne są wszystkie zmiany od ostatniej posiadanej wersji",
            ]),
        ],
    },
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
