"""
config.py
---------
Centralne stałe konfiguracyjne aplikacji Odbiory.
Wszystkie ścieżki, wersje i parametry globalne trzymamy tutaj,
aby uniknąć rozrzucania "magic strings" po całej bazie kodu.
"""

import os
import sys
from pathlib import Path

# --- Ścieżki ---
# W zbudowanym EXE zasoby są w katalogu _MEIPASS (obok exe dla --onedir).
# Dane użytkownika (baza, backupy) trafiają do AppData żeby nie wymagać
# uprawnień administratora przy zapisie do Program Files.
if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    DATA_DIR   = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Odbiory"
else:
    BUNDLE_DIR = Path(__file__).resolve().parent
    DATA_DIR   = BUNDLE_DIR / "data"

BASE_DIR   = BUNDLE_DIR
STYLES_DIR = BUNDLE_DIR / "resources" / "styles"
DB_PATH    = DATA_DIR / "odbiory.db"
BACKUP_DIR = DATA_DIR / "backups"

DATA_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# --- Metadane aplikacji ---
APP_NAME = "Odbiory - System Zarządzania Montażami"
APP_VERSION = "1.0.2"
APP_AUTHOR = "filipzarazinski"

# --- GitHub / Aktualizacje ---
GITHUB_OWNER   = "filipzarazinski"
GITHUB_REPO    = "Aplikacja-do-odbior-w"
VERSION_URL    = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/version.txt"
INSTALLER_URL  = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest/download/Odbiory_Setup.exe"

# --- Parametry UI ---
MAIN_WINDOW_MIN_WIDTH = 1280
MAIN_WINDOW_MIN_HEIGHT = 720
FORM_WIDTH = 1050
FORM_HEIGHT = 820

# --- Stałe domenowe ---
# Checkboxy CAN (CheckBox51–58 w VBA) – odpowiadają polom CAN z CanConfig.bas:
# CB51=Prędkość, CB52=Obroty, CB53=Dystans, CB54=Paliwo,
# CB55=Zużycie, CB56=Driver/Statusy, CB57=Statusy2, CB58=Webasto
CAN_CHECKBOX_LABELS  = [
    "Prędkość",      # CB51 – canPredkosc
    "Obroty",        # CB52 – canObroty
    "Dystans",       # CB53 – canDystans
    "Paliwo",        # CB54 – canPaliwo
    "Zużycie",       # CB55 – canZuzycie
    "Kierowca",      # CB56 – canDriver
    "Statusy",       # CB57 – canStatusy
    "Webasto",       # CB58 – canWebasto
]
CAN_JSON_KEYS = [
    "canPredkosc", "canObroty", "canDystans", "canPaliwo",
    "canZuzycie",  "canDriver", "canStatusy", "canWebasto",
]
# Typ połączenia CAN: OptionButton10=cancliq (Ciężarowy), OptionButton11=bramkafms (Osobowy)
CAN_CONNECTION_TRUCK = "cancliq"
CAN_CONNECTION_CAR   = "bramkafms"

# Typy pojazdów (połączona lista)
VEHICLE_TYPES = ["Ciężarowy", "Osobowy", "Maszyna", "Naczepa"]

# Opcje D8 (podłączenie tachografu) – z formInitialize.bas → ComboBox3
D8_OPTIONS = ["Tachoreader", "FMB640/FMC650", "Brak"]

# Marki tachografu – z formEdit.bas i Submit.bas
TACHO_BRANDS_TACHOREADER  = ["Siemens", "Stonerige"]
TACHO_BRANDS_FMB640       = ["Siemens", "Stoneridge", "Inne"]

# Funkcje DIN wymagające pola S/N (z logiki ComboBox_Change w VBA)
DIN_NEEDS_SN_KEYWORDS = ["zabezpieczenie", "wlew"]
