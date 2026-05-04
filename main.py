import sys
import logging

from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from config import APP_NAME, APP_VERSION, STYLES_DIR, DB_PATH, BASE_DIR, BACKUP_DIR, VERSION_URL, INSTALLER_URL
from database.db_manager import DatabaseManager
from ui.main_window import MainWindow


class UpdateChecker(QThread):
    update_available = Signal(str)   # emits latest version string

    def run(self):
        try:
            import requests
            r = requests.get(VERSION_URL, timeout=6)
            r.raise_for_status()
            latest = r.text.strip()
            if self._newer(latest, APP_VERSION):
                self.update_available.emit(latest)
        except Exception:
            pass

    @staticmethod
    def _newer(a: str, b: str) -> bool:
        def t(v):
            try:
                return tuple(int(x) for x in v.split("."))
            except ValueError:
                return (0,)
        return t(a) > t(b)


def _download_and_install(parent, version: str) -> None:
    import os, tempfile, subprocess
    tmp_path = os.path.join(tempfile.gettempdir(), "Odbiory_Setup.exe")
    progress = QProgressDialog(f"Pobieranie wersji {version}…", "Anuluj", 0, 100, parent)
    progress.setWindowTitle("Aktualizacja")
    progress.setWindowModality(Qt.WindowModal)
    progress.setMinimumWidth(340)
    progress.setValue(0)
    progress.show()
    try:
        import requests
        r = requests.get(INSTALLER_URL, stream=True, timeout=60)
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if progress.wasCanceled():
                    return
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    progress.setValue(int(downloaded * 100 / total))
                QApplication.processEvents()
    except Exception as e:
        QMessageBox.critical(parent, "Błąd pobierania", f"Nie udało się pobrać aktualizacji:\n{e}")
        return
    finally:
        progress.close()
    import sys as _sys
    exe_path = _sys.executable if getattr(_sys, "frozen", False) else None

    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE

    if exe_path:
        # Start-Process -Wait correctly waits for UAC-elevated installer to finish
        ps_cmd = (
            f"Start-Process '{tmp_path}' "
            f"-ArgumentList '/VERYSILENT /CLOSEAPPLICATIONS' -Wait; "
            f"Start-Process '{exe_path}'"
        )
        cmd = f'powershell -WindowStyle Hidden -Command "{ps_cmd}"'
    else:
        cmd = f'"{tmp_path}" /VERYSILENT /CLOSEAPPLICATIONS'

    subprocess.Popen(
        cmd,
        shell=True,
        startupinfo=si,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    QApplication.quit()


def setup_logging() -> None:
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    from config import DATA_DIR
    log_path = DATA_DIR / "odbiory.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(log_path), encoding="utf-8"),
        ],
    )


def load_stylesheet(app: QApplication, is_light: bool) -> None:
    """Ładuje odpowiedni plik QSS w zależności od wybranego motywu."""
    theme_filename = "theme_light.qss" if is_light else "theme.qss"
    qss_file = STYLES_DIR / theme_filename

    if qss_file.exists():
        stylesheet = qss_file.read_text(encoding="utf-8")
        app.setStyleSheet(stylesheet)
        logging.getLogger(__name__).info(f"Styl załadowany: {qss_file}")
    else:
        logging.getLogger(__name__).warning(f"Brak pliku QSS: {qss_file}")
        
        # Dynamiczny fallback w razie braku pliku fizycznego
        if is_light:
            app.setStyleSheet("* { font-family: 'Segoe UI'; font-size: 9pt; color: #0f172a; } QMainWindow, QDialog, QWidget { background-color: #f8fafc; }")
        else:
            app.setStyleSheet("* { font-family: 'Segoe UI'; font-size: 9pt; color: #e2e8f0; } QMainWindow, QDialog, QWidget { background-color: #0f1115; }")


def main() -> int:
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info(f"Uruchamianie {APP_NAME} v{APP_VERSION}")

    try:
        info_file = BASE_DIR / "data_path.txt"
        info_file.write_text(
            f"Baza danych: {DB_PATH}\n"
            f"Kopie zapasowe: {BACKUP_DIR}\n",
            encoding="utf-8",
        )
    except Exception:
        pass

    # Windows DPI scaling
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    _INSTANCE_KEY = "Odbiory_SingleInstance"
    _sock = QLocalSocket()
    _sock.connectToServer(_INSTANCE_KEY)
    if _sock.waitForConnected(300):
        _sock.disconnectFromServer()
        QMessageBox.warning(None, "Aplikacja już działa",
                            f"{APP_NAME} jest już uruchomiona.\nSprawdź pasek zadań.")
        return 1
    _instance_server = QLocalServer()
    QLocalServer.removeServer(_INSTANCE_KEY)
    _instance_server.listen(_INSTANCE_KEY)
    app.setFont(QFont("Segoe UI", 9))

    # Ustawienie ikony aplikacji
    logo_path = BASE_DIR / "logo.png"
    if logo_path.exists():
        app.setWindowIcon(QIcon(str(logo_path)))
        
        # Wymuszenie własnej ikony na pasku zadań w systemie Windows
        if sys.platform == "win32":
            import ctypes
            myappid = f"filipzarazinski.Odbiory.app.{APP_VERSION}"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

    # 1. Inicjalizacja bazy PRZED załadowaniem stylów
    try:
        db = DatabaseManager.instance()
        logger.info(f"Baza danych: {DB_PATH}")
    except Exception as exc:
        logger.critical(f"Błąd bazy danych: {exc}", exc_info=True)
        QMessageBox.critical(None, "Błąd krytyczny",
                             f"Nie można otworzyć bazy danych:\n{exc}\n\nŚcieżka: {DB_PATH}")
        return 1

    # 2. Odczyt motywu i ładowanie globalnych stylów
    is_light = db.get_setting("theme_mode", "dark") == "light"
    load_stylesheet(app, is_light)

    try:
        window = MainWindow()
        window.show()

        def _on_update(latest: str):
            reply = QMessageBox.question(
                window, "Dostępna nowa wersja",
                f"Dostępna wersja:  {latest}\n"
                f"Twoja wersja:      {APP_VERSION}\n\n"
                "Pobrać i zainstalować teraz?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                _download_and_install(window, latest)

        _checker = UpdateChecker()
        _checker.update_available.connect(_on_update)
        _checker.start()

        last_seen = db.get_setting("last_seen_version", "")
        if last_seen != APP_VERSION:
            db.set_setting("saved_search_filter", "")
            db.set_setting("saved_date_from", "")
            db.set_setting("saved_date_to", "")
            from ui.whats_new_dialog import WhatsNewDialog
            WhatsNewDialog(APP_VERSION, last_seen, window).exec()
            db.set_setting("last_seen_version", APP_VERSION)

    except Exception as exc:
        logger.critical(f"Błąd okna głównego: {exc}", exc_info=True)
        QMessageBox.critical(None, "Błąd krytyczny", f"Nie można otworzyć aplikacji:\n{exc}")
        return 1

    exit_code = app.exec()

    # Auto-backup przy zamknięciu
    import shutil, os
    from datetime import datetime
    auto_backup_path = db.get_setting("auto_backup_path", "").strip()
    if auto_backup_path:
        try:
            os.makedirs(auto_backup_path, exist_ok=True)
            backup_name = f"odbiory_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            shutil.copy2(DB_PATH, os.path.join(auto_backup_path, backup_name))
            logger.info(f"Auto-backup: {os.path.join(auto_backup_path, backup_name)}")
        except Exception as exc:
            logger.warning(f"Auto-backup nie powiódł się: {exc}")

    db.close()
    logger.info("Aplikacja zamknięta.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
