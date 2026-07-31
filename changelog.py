# changelog.py - Historia wersji aplikacji. Najnowsza wersja na gorze listy.

CHANGELOG = [
    {
        "version": "1.2.2",
        "date": "2026-07-31",
        "entries": [
            ("Formularz", [
                "Nowy przycisk 'Panel' - otwiera panel GPS (lub dedykowany panel danej floty, np. Tauron/PGE/MPWiK) i kopiuje ID urządzenia do schowka",
                "Aby przycisk 'Panel' działał, zaimportuj linki: pobierz plik Excel 'Baza' i zaimportuj go w Ustawienia -> Słowniki -> Słowniki/Importy -> Linki flot -> Importuj z Excel",
                "Przyciski 'Flota' i 'Panel' są nieaktywne, gdy formularz nie ma przypisanej floty",
                "Pola 'Nr boczny' i 'CCID' mają teraz przycisk kopiowania do schowka",
                "Nowe pole 'Pojemność zbiorników' w zakładce CAN",
                "Zmiana CCID zawsze aktualizuje pole SIM na numer przypisany do tego CCID (nawet gdy SIM był już wypełniony)",
                "Pola 'Gdzie rejestrator' i 'Marka/model' (razem z wybranym Typem pojazdu) mają przycisk '+' dodający wpisaną wartość do słownika bez wychodzenia z formularza - z krótkim podświetleniem i komunikatem po dodaniu",
            ]),
            ("Lista główna", [
                "Poprawiono trafialność kliknięcia w checkbox kolumny 'Odebrano' - reaguje na kliknięcie w całą komórkę, nie tylko w samą ikonkę",
                "Menu kontekstowe (prawy klik) ma teraz też opcję 'Przejdź do panelu', analogiczną do 'Przejdź do floty'",
            ]),
            ("Ustawienia", [
                "Słownik Pojazdy: pole 'Typ pojazdu' w dialogu Dodaj/Edytuj jest teraz listą wyboru (Ciężarowy/Osobowy/Maszyna/Naczepa) - nie można już wpisać własnej wartości",
            ]),
            ("Inne", [
                "Poprawka błędów",
                "Aby nowe funkcjonalności działały poprawnie, należy zaktualizować skrypt do uzupełniania protokołu w zakładce Tampermonkey",
            ]),
        ],
    },
    {
        "version": "1.2.1",
        "date": "2026-05-12",
        "entries": [
            ("Inne", [
                "Poprawka błędów",
            ]),
        ],
    },
    {
        "version": "1.2.0",
        "date": "2026-05-11",
        "entries": [
            ("Lista główna", [
                "Filtry kolumnowe – kliknij ▼ w nagłówku kolumny aby filtrować wartości jak w Excelu",
                "Prawy klik na nagłówku kolumny – opcja wyczyszczenia wszystkich filtrów kolumn",
                "Prawy klik na wierszu: nowy układ sekcji – Przejdź do floty / Odśwież, Edytuj, Duplikuj, Usuń / Kopiuj do dyżurów",
                "F5 odświeża tabelę z zachowaniem aktywnych filtrów",
            ]),
        ],
    },
    {
        "version": "1.1.9",
        "date": "2026-05-06",
        "entries": [
            ("Lista główna", [
                "Wyczyszczenie wyszukiwarki przyciskiem X odświeża tabelę automatycznie",
            ]),
            ("Ustawienia", [
                "Zakładka 'Tabela główna' podzielona na podzakładki: Kolumny, Szybkie filtry i Kolory",
                "Szybkie filtry: sekcja Daty z możliwością ukrycia i zmiany kolejności przycisków",
                "Szybkie filtry: sekcja Wyszukiwarka – w pełni edytowalna lista własnych filtrów z możliwością zmiany kolejności",
            ]),
        ],
    },
    {
        "version": "1.1.8",
        "date": "2026-05-06",
        "entries": [
            ("Formularz", [
                "Ctrl+W zamyka formularz",
                "Drobne poprawki zapisu formularza",
            ]),
        ],
    },
    {
        "version": "1.1.7",
        "date": "2026-05-05",
        "entries": [
            ("Inne", [
                "Drobne poprawki",
                "Poprawa motywu jasnego",
            ]),
        ],
    },
    {
        "version": "1.1.6",
        "date": "2026-05-04",
        "entries": [
            ("Inne", [
                "Drobne poprawki",
            ]),
        ],
    },
    {
        "version": "1.1.5",
        "date": "2026-05-03",
        "entries": [
            ("Formularz", [
                "Ctrl+Shift+V oraz 'Wklej bez formatowania' w menu kontekstowym w polach komentarza – wkleja czysty tekst bez formatowania HTML",
            ]),
            ("Inne", [
                "Próba uruchomienia drugiej instancji aplikacji wyświetla komunikat zamiast otwierać duplikat",
                "Poprawa trybu jasnego",
            ]),
        ],
    },
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
