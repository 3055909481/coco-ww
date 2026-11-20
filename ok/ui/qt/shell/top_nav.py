"""Top navigation: page tabs in the title bar instead of the sidebar.

The app config picks the layout (``'top_nav'`` in ``config.py``)::

    'top_nav': {
        'tabs': ['HomeTab', 'TasksPage', ...],   # tabs left of the title bar, in this order
        'actions': ['DashboardTab'],              # buttons on the right of the title bar
        'labels': {'TasksPage': '任务设置'},       # tab text overrides (default: the page name)
        'accent': '#F2C14E',                      # underline of the current tab
    }

Pages are matched by class name. Every other page goes into the menu behind
the gear button, pinned pages (Settings, About) last. MainWindow still keeps
the sidebar model (hidden) so the rest of the window code is unchanged.
"""

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QWidget

from ok.ui.qt.kit.buttons import Button
from ok.ui.qt.kit.icons import icon as kit_icon
from ok.ui.qt.kit.theme import theme
from ok.ui.qt.kit.window import TITLE_BAR_HEIGHT

# Menu groups follow the sidebar sections they would have been in; pinned pages (bottom) go last.
SECTION_ORDER = ('run', 'game', 'develop')
TAB_PADDING = 12
TAB_FONT_PX = 14
UNDERLINE_HEIGHT = 2.5
UNDERLINE_GAP = 7  # from the bottom of the title bar


def page_class(page) -> str:
    return type(page).__name__


class TopTab(QPushButton):
    """A text tab; the current one is bold with an accent underline. Painted by hand
    so it reads the same in the light and dark themes."""

    def __init__(self, key: str, text: str, accent: QColor, parent=None):
        super().__init__(parent)
        self.key = key
        self._label = text
        self._accent = accent
        self._badge = False
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.toggled.connect(self.update)
        theme.changed.connect(self.update)

    def label(self) -> str:
        return self._label

    def set_label(self, text: str) -> None:
        self._label = text
        self.updateGeometry()
        self.update()

    def set_badge(self, visible: bool) -> None:
        self._badge = bool(visible)
        self.update()

    def _font(self, bold: bool) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(TAB_FONT_PX)
        font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
        return font

    def sizeHint(self) -> QSize:
        # Measured bold so the row does not shift when the selection moves.
        width = QFontMetrics(self._font(True)).horizontalAdvance(self._label)
        return QSize(width + TAB_PADDING * 2, max(self.height(), 30))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        checked = self.isChecked()
        hover = self.underMouse()
        painter.setFont(self._font(checked))
        painter.setPen(theme.color('text') if checked or hover else theme.color('text_secondary'))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._label)
        metrics = QFontMetrics(self._font(checked))
        text_width = metrics.horizontalAdvance(self._label)
        left = (self.width() - text_width) / 2
        if checked:
            line = QRectF(left, self.height() - UNDERLINE_GAP - UNDERLINE_HEIGHT, text_width, UNDERLINE_HEIGHT)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._accent)
            painter.drawRoundedRect(line, UNDERLINE_HEIGHT / 2, UNDERLINE_HEIGHT / 2)
        if self._badge:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._accent)
            painter.drawEllipse(QRectF(left + text_width + 2, self.height() / 2 - metrics.ascent() / 2 - 4, 6, 6))


class TopNav(QWidget):
    """Tabs (left), action buttons and the "more" menu (right) for the title bar.

    ``tabs_bar`` and ``right_bar`` are placed into the title bar by MainWindow.
    ``current_changed`` carries the key of the page the user picked.
    """

    current_changed = Signal(str)

    def __init__(self, spec: dict, parent=None):
        super().__init__(parent)
        self.hide()  # only a holder; the two bars below go into the title bar
        self._tab_order = list(spec.get('tabs') or ())
        self._action_order = list(spec.get('actions') or ())
        self._labels = dict(spec.get('labels') or {})
        self._accent = QColor(spec.get('accent') or '#F2C14E')
        self._tabs = {}  # key -> TopTab
        self._actions = {}  # key -> Button
        self._menu_pages = []  # [(key, text, icon, group)]
        self._badges = set()
        self._current = None

        self.tabs_bar = QWidget()
        self.tabs_bar.setObjectName('topNavTabs')
        self._tabs_layout = QHBoxLayout(self.tabs_bar)
        self._tabs_layout.setContentsMargins(14, 0, 0, 0)
        self._tabs_layout.setSpacing(2)

        self.right_bar = QWidget()
        self.right_bar.setObjectName('topNavRight')
        self._right_layout = QHBoxLayout(self.right_bar)
        self._right_layout.setContentsMargins(0, 0, 6, 0)
        self._right_layout.setSpacing(6)
        self.more_button = Button('', 'gear', 'subtle', 'small')
        self.more_button.setToolTip(self.tr('More'))
        self.more_button.clicked.connect(self._open_menu)
        self._right_layout.addWidget(self.more_button)

    # --- pages ----------------------------------------------------------------

    def placement(self, page) -> str:
        name = page_class(page)
        if name in self._tab_order:
            return 'tab'
        if name in self._action_order:
            return 'action'
        return 'menu'

    def add_page(self, key: str, page, text: str, icon=None, section: str = None, bottom: bool = False) -> None:
        placement = self.placement(page)
        name = page_class(page)
        label = self._labels.get(name) or text
        if placement == 'tab':
            tab = TopTab(key, label, self._accent, self.tabs_bar)
            tab.setFixedHeight(TITLE_BAR_HEIGHT)
            tab.clicked.connect(lambda _=False, k=key: self.current_changed.emit(k))
            rank = self._tab_order.index(name)
            index = sum(1 for other in self._tabs.values() if other.property('rank') < rank)
            tab.setProperty('rank', rank)
            self._tabs[key] = tab
            self._tabs_layout.insertWidget(index, tab)
            tab.setChecked(key == self._current)
        elif placement == 'action':
            button = Button(label, icon, 'secondary', 'small')
            button.clicked.connect(lambda _=False, k=key: self.current_changed.emit(k))
            rank = self._action_order.index(name)
            button.setProperty('rank', rank)
            index = sum(1 for other in self._actions.values() if other.property('rank') < rank)
            self._actions[key] = button
            self._right_layout.insertWidget(index, button)
        else:
            group = len(SECTION_ORDER) if bottom else (
                SECTION_ORDER.index(section) if section in SECTION_ORDER else len(SECTION_ORDER) - 1)
            self._menu_pages.append((key, label, icon, group))
        self._refresh_badges()

    def remove_page(self, key: str) -> None:
        widget = self._tabs.pop(key, None) or self._actions.pop(key, None)
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        self._menu_pages = [entry for entry in self._menu_pages if entry[0] != key]
        self._badges.discard(key)
        self._refresh_badges()

    def has_page(self, key: str) -> bool:
        return key in self._tabs or key in self._actions or any(entry[0] == key for entry in self._menu_pages)

    def tab_labels(self) -> list:
        return [tab.label() for tab in sorted(self._tabs.values(), key=lambda t: t.property('rank'))]

    def action_labels(self) -> list:
        return [button.text() for button in sorted(self._actions.values(), key=lambda b: b.property('rank'))]

    def _menu_groups(self) -> list:
        groups = {}
        for entry in self._menu_pages:
            groups.setdefault(entry[3], []).append(entry)
        return [groups[group] for group in sorted(groups)]

    def menu_labels(self) -> list:
        return [[text for _, text, _, _ in group] for group in self._menu_groups()]

    # --- state ----------------------------------------------------------------

    def set_current(self, key: str) -> None:
        self._current = key
        for tab_key, tab in self._tabs.items():
            tab.setChecked(tab_key == key)
        for action_key, button in self._actions.items():
            button.setProperty('variant', 'tonal' if action_key == key else 'secondary')
            button.style().unpolish(button)
            button.style().polish(button)

    def set_badge(self, key: str, visible: bool) -> None:
        if visible:
            self._badges.add(key)
        else:
            self._badges.discard(key)
        self._refresh_badges()

    def _refresh_badges(self) -> None:
        for key, tab in self._tabs.items():
            tab.set_badge(key in self._badges)
        in_menu = any(entry[0] in self._badges for entry in self._menu_pages)
        self.more_button.setProperty('badge', in_menu)
        self.more_button.setToolTip(self.tr('More') + (' ●' if in_menu else ''))

    # --- menu -----------------------------------------------------------------

    def build_menu(self) -> QMenu:
        menu = QMenu(self.right_bar)
        theme.install(menu)
        for group in self._menu_groups():
            if not menu.isEmpty():
                menu.addSeparator()
            for key, text, icon, _ in group:
                label = text + ('  ●' if key in self._badges else '')
                action = menu.addAction(label)
                if isinstance(icon, str):
                    action.setIcon(kit_icon(icon, 'text_secondary'))
                elif icon is not None:
                    action.setIcon(icon)
                action.setCheckable(True)
                action.setChecked(key == self._current)
                action.triggered.connect(lambda _=False, k=key: self.current_changed.emit(k))
        return menu

    def _open_menu(self) -> None:
        menu = self.build_menu()
        button = self.more_button
        position = button.mapToGlobal(button.rect().bottomRight())
        position.setX(position.x() - menu.sizeHint().width())
        menu.exec(position)
        menu.deleteLater()
