import os
import sys

src_file = "quick_kv.py"
base_dir = "src"

with open(src_file, 'r', encoding='utf-8') as f:
    lines = f.readlines()

def l(start, end):
    return "".join(lines[start-1:end])

# Create directories
for d in ['', 'core', 'utils', 'ui', 'services', 'resources']:
    os.makedirs(os.path.join(base_dir, d), exist_ok=True)

# 0. COMMON IMPORTS
common_imports = l(1, 39)

# 1. UTILS/PATHS.PY
with open(os.path.join(base_dir, 'utils', 'paths.py'), 'w', encoding='utf-8') as f:
    f.write('''import os
import sys

def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        # Move up from utils/ to src/ then to root
        # Actually, let's keep the config/db in the current working directory or one level up
        # If running from root, abspath(__file__) is root/src/utils/paths.py -> root/
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, "src/resources", relative_path)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "resources", relative_path)
''')

# 2. CORE/CONFIG.PY
config_py = common_imports + r'''
from utils.paths import get_base_path, resource_path

''' + l(41, 86) + r'''
BASE_PATH = get_base_path()
WORD_FILE = os.path.join(BASE_PATH, "词库.md")
CONFIG_FILE = os.path.join(BASE_PATH, "config.ini")
AUTO_LOAD_DIR = os.path.join(BASE_PATH, "MD词库-需自动载入的请放入")
HISTORY_FILE = os.path.join(BASE_PATH, "剪贴板词库-勿删.md")
CLIPBOARD_FILE = os.path.join(BASE_PATH, "剪贴板词库.md")
HELP_DOCS_DIR = os.path.join(BASE_PATH, "帮助文档")

ICON_PATH = resource_path("icon.png")

DEBUG_MODE = True
VERSION = "1.2.0" # Updated version

''' + l(120, 122) + l(123, 142)

with open(os.path.join(base_dir, 'core', 'config.py'), 'w', encoding='utf-8') as f:
    f.write(config_py)

# 3. CORE/SETTINGS.PY
settings_py = common_imports + r'''
from core.config import *
''' + l(250, 353)
with open(os.path.join(base_dir, 'core', 'settings.py'), 'w', encoding='utf-8') as f:
    f.write(settings_py)

# 4. CORE/WORD_SOURCE.PY
word_source_py = common_imports + r'''
from core.config import *
''' + l(354, 492)
with open(os.path.join(base_dir, 'core', 'word_source.py'), 'w', encoding='utf-8') as f:
    f.write(word_source_py)

# 5. CORE/WORD_MANAGER.PY
word_manager_py = common_imports + r'''
from core.config import *
from core.word_source import WordSource
''' + l(493, 893)
with open(os.path.join(base_dir, 'core', 'word_manager.py'), 'w', encoding='utf-8') as f:
    f.write(word_manager_py)

# 6. UI/DELEGATES.PY
delegates_py = common_imports + r'''
from core.config import *
''' + l(143, 249)
with open(os.path.join(base_dir, 'ui', 'delegates.py'), 'w', encoding='utf-8') as f:
    f.write(delegates_py)

# 7. UI/COMPONENTS.PY
components_py = common_imports + r'''
from core.config import *
''' + l(894, 951) + l(954, 1034) + l(1035, 1069) + l(1071, 1135) + l(1137, 1173) + l(1174, 1252)
with open(os.path.join(base_dir, 'ui', 'components.py'), 'w', encoding='utf-8') as f:
    f.write(components_py)

# 8. UI/SEARCH_POPUP.PY
search_popup_py = common_imports + r'''
from core.config import *
from ui.delegates import StyledItemDelegate
from ui.components import EditDialog, ScrollableMessageBox
''' + l(1253, 1630)
with open(os.path.join(base_dir, 'ui', 'search_popup.py'), 'w', encoding='utf-8') as f:
    f.write(search_popup_py)

# 9. SERVICES/HOTKEY_MANAGER.PY
hotkey_manager_py = common_imports + r'''
from core.config import *
''' + l(1631, 1715)
with open(os.path.join(base_dir, 'services', 'hotkey_manager.py'), 'w', encoding='utf-8') as f:
    f.write(hotkey_manager_py)

# 10. SERVICES/SHORTCUT_LISTENER.PY
shortcut_listener_py = common_imports + r'''
from core.config import *
''' + l(1716, 1813)
with open(os.path.join(base_dir, 'services', 'shortcut_listener.py'), 'w', encoding='utf-8') as f:
    f.write(shortcut_listener_py)

# 11. MAIN_CONTROLLER.PY
main_controller_py = common_imports + r'''
import builtins
# Dependency Injection
from core.config import *
from ui.search_popup import SearchPopup
from ui.components import HotkeyDialog, DisclaimerDialog, ScrollableMessageBox, get_disclaimer_html_text
from services.hotkey_manager import NativeHotkeyManager
from services.shortcut_listener import ShortcutListener
from PySide6.QtNetwork import QLocalServer, QLocalSocket

''' + l(1814, 1837) + l(1838, 2656)
with open(os.path.join(base_dir, 'main_controller.py'), 'w', encoding='utf-8') as f:
    f.write(main_controller_py)

# 12. MAIN.PY
main_py = common_imports + r'''
from core.config import *
from core.settings import SettingsManager
from core.word_manager import WordManager
from main_controller import MainController
from ui.components import DisclaimerDialog
from PySide6.QtNetwork import QLocalServer, QLocalSocket

''' + l(2657, 2848)
with open(os.path.join(base_dir, 'main.py'), 'w', encoding='utf-8') as f:
    f.write(main_py)

print("Files successfully split.")
