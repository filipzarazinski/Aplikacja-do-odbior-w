import sys
import logging

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon

from config import APP_NAME, APP_VERSION, STYLES_DIR, DB_PATH, BASE_DIR, BACKUP_DIR
from database.db_manager import DatabaseManager
from ui.main_window import MainWindow


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
    except Exception as exc:
        logger.critical(f"Błąd okna głównego: {exc}", exc_info=True)
        QMessageBox.critical(None, "Błąd krytyczny", f"Nie można otworzyć aplikacji:\n{exc}")
        return 1

    exit_code = app.exec()
    db.close()
    logger.info("Aplikacja zamknięta.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
