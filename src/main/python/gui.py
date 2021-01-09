import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import abc
import time

import cv2
from PyQt5 import QtGui, QtWidgets

from PyQt5.QtCore import QDateTime, QStandardPaths, QFile, QFileInfo, Qt, QObject, QThread, pyqtSignal, QTimer, \
    QSettings, QCoreApplication
from PyQt5.QtGui import QPixmap
from fbs_runtime.application_context.PyQt5 import ApplicationContext
from PyQt5.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel, QListWidget, QPushButton, QHBoxLayout, \
    QTabWidget, QTextEdit, QApplication, QProgressBar, QFileDialog, QMessageBox, QLineEdit, QTableWidget, QSpinBox, \
    QHeaderView, QTableWidgetItem, QAbstractItemView, QSplitter, QCheckBox, QMenu
from PyQt5.QtCore import QSettings, QPoint

from pytesseract import pytesseract, Output
import api_interface


class Indexer(QWidget):
    def __init__(self, wheres_the_fck_receipt: api_interface.WheresTheFckReceipt, parent=None):
        QWidget.__init__(self, parent=None)
        self.wheres_the_fck_receipt = wheres_the_fck_receipt
        self.index_job = None  # type: api_interface.IndexJob
        self.index_job_timer = QTimer()
        self.index_job_timer.timeout.connect(self.index_job_timer_timeout)

        # WIDGETS
        # add dir button
        self.add_directory = QPushButton('Add Directory')
        self.add_directory.setEnabled(True)
        self.add_directory.clicked.connect(self.add_directory_clicked)

        # locations
        self.directories = QListWidget()
        self.directories.itemSelectionChanged.connect(self.directories_selection_changed)
        self.directories.setSelectionMode(QAbstractItemView.SingleSelection)
        for dir in self.wheres_the_fck_receipt.get_directories():
            self.directories.addItem(dir)

        # the locations_action_bar
        self.index = QPushButton('Update')
        self.index.clicked.connect(self.update_clicked)
        self.remove_dir = QPushButton('Remove')
        self.remove_dir.clicked.connect(self.remove_clicked)
        self.re_index = QPushButton('Re-Index')
        self.re_index.clicked.connect(self.reindex_clicked)
        file_list_action_bar_layout = QHBoxLayout()
        file_list_action_bar_layout.setContentsMargins(0, 0, 0, 0)
        file_list_action_bar_layout.addWidget(self.index)
        file_list_action_bar_layout.addWidget(self.remove_dir)
        file_list_action_bar_layout.addWidget(self.re_index)
        self.file_list_action_bar_widget = QWidget()
        self.file_list_action_bar_widget.setLayout(file_list_action_bar_layout)
        self.file_list_action_bar_widget.setEnabled(False)

        # index_status_widget
        self.index_progress = QProgressBar()
        self.index_progress.setEnabled(False)
        self.stop_index = QPushButton('Stop Indexing')
        self.stop_index.setEnabled(False)
        self.stop_index.clicked.connect(self.stop_index_clicked)
        index_status_widget_layout = QHBoxLayout()
        index_status_widget_layout.setContentsMargins(0, 0, 0, 0)
        index_status_widget_layout.addWidget(self.index_progress)
        index_status_widget_layout.addWidget(self.stop_index)
        index_status_widget = QWidget()
        index_status_widget.setLayout(index_status_widget_layout)

        # index console
        self.index_console = QTextEdit()
        self.index_console.setReadOnly(True)
        # self.index_console.setEnabled(False)

        # layout
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Indexed Directories:"))
        layout.addWidget(self.add_directory)
        layout.addWidget(self.directories)
        layout.addWidget(self.file_list_action_bar_widget)
        layout.addWidget(QLabel("Indexer Status:"))
        layout.addWidget(index_status_widget)
        layout.addWidget(self.index_console)
        self.setLayout(layout)

    def directories_selection_changed(self):
        list_items = self.directories.selectedItems()
        self.file_list_action_bar_widget.setEnabled(len(list_items) == 1)

    def add_directory_clicked(self):

        settings = QSettings('WheresTheFckReceipt', 'WheresTheFckReceipt')
        last_directory_added = settings.value("last_directory_added", "")

        directory = str(QFileDialog.getExistingDirectory(self, "Select Directory",
                                                         last_directory_added,
                                                         QFileDialog.ShowDirsOnly))
        if directory:
            settings.setValue("last_directory_added", directory)
            del settings
            # get the job
            if not self.directories.findItems(directory, Qt.MatchExactly):
                self.directories.addItem(directory)
            self.index_job = self.wheres_the_fck_receipt.add_directory(directory)
            self.run_indexer()

    def run_indexer(self):
        # manage gui
        self.add_directory.setEnabled(False)
        self.directories.setEnabled(False)
        self.file_list_action_bar_widget.setEnabled(False)
        self.index_progress.setEnabled(True)
        self.index_progress.reset()
        self.stop_index.setEnabled(True)
        # self.index_console.setEnabled(True)
        self.index_console.clear()
        # start job
        self.stop_index.setEnabled(True)
        self.index_job.start()
        self.index_job_timer.start(500)

    def update_clicked(self):
        self.index_job = self.wheres_the_fck_receipt.update_directory(self.directories.currentItem().text())
        self.run_indexer()

    def remove_clicked(self):
        self.wheres_the_fck_receipt.remove_directory(self.directories.currentItem().text())
        self.directories.takeItem(self.directories.currentRow())

    def reindex_clicked(self):
        self.index_job = self.wheres_the_fck_receipt.reindex_directory(self.directories.currentItem().text())
        self.run_indexer()

    def stop_index_clicked(self):
        self.index_job.stop()
        self.indexing_stopped()

    def indexing_stopped(self):
        self.index_job_timer.stop()
        self.add_directory.setEnabled(True)
        self.directories.setEnabled(True)
        self.file_list_action_bar_widget.setEnabled(len(self.directories.selectedItems()) > 0)
        self.index_progress.setEnabled(False)
        self.stop_index.setEnabled(False)
        # self.index_console.setEnabled(False)
        self.index_job = None

    def index_job_timer_timeout(self):
        for msg in self.index_job.get_messages():
            self.index_console.append(msg)
        num_files = self.index_job.get_num_files()
        if num_files and self.index_progress.maximum() != num_files:
            self.index_progress.setRange(0, num_files)
        curr_file_idx = self.index_job.get_curr_file_index()
        if curr_file_idx:
            self.index_progress.setValue(curr_file_idx)
        if self.index_job.is_finished():
            self.index_progress.setValue(self.index_progress.maximum())
            self.indexing_stopped()


class MatcherTableWidget(QTableWidget):

    def __init__(self, parent=None):
        QTableWidget.__init__(self, parent)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        save_action = menu.addAction("Save")
        action = menu.exec_(self.mapToGlobal(event.pos()))
        if action == save_action and self.rowCount() > 0:
            current = self.currentRow()
            if current is not None:
                path = self.item(current, 3).text()
                name = self.item(current, 0).text()
                full_path = path + "/" + name
                ext = os.path.splitext(name)[1]

                settings = QSettings('WheresTheFckReceipt', 'WheresTheFckReceipt')
                save_file_path = settings.value("save_file_path", full_path)
                if save_file_path != full_path:
                    save_file_path = save_file_path + "/" + name
                new_path = QFileDialog.getSaveFileName(self, 'Save File', save_file_path, filter="*" + ext)
                if new_path[0]:
                    settings.setValue("save_file_path", os.path.dirname(new_path[0]))
                    shutil.copyfile(full_path, new_path[0])


class SearcherWidget(QWidget):
    def __init__(self, wheres_the_fck_receipt: api_interface.WheresTheFckReceipt, parent=None):
        QWidget.__init__(self, parent)
        self.wheres_the_fck_receipt = wheres_the_fck_receipt  # type: api_interface.WheresTheFckReceipt
        self.results = None  # type List[api_interface.Result]
        self.current_preview_image = None

        # query
        self.query = QLineEdit()
        self.query.mousePressEvent = lambda _: self.query.selectAll()
        self.query.returnPressed.connect(self.search_button_clicked)
        self.limit_box = QSpinBox()
        self.limit_box.setValue(int(self.wheres_the_fck_receipt.get_settings()["default_limit"][0]))
        self.limit_box.valueChanged.connect(self.search_button_clicked)
        self.cs_box = QCheckBox("Case Sensitive")
        self.cs_box.stateChanged.connect(self.search_button_clicked)
        search_button = QPushButton('Search')
        search_button.clicked.connect(self.search_button_clicked)

        query_bar_layout = QHBoxLayout()
        query_bar_layout.setContentsMargins(0, 0, 0, 0)
        query_bar_layout.addWidget(QLabel("Search Term"))
        query_bar_layout.addWidget(self.query)
        query_bar_layout.addWidget(QLabel("Max. Results"))
        query_bar_layout.addWidget(self.limit_box)
        query_bar_layout.addWidget(self.cs_box)
        query_bar_layout.addWidget(search_button)

        # the file_list
        self.match_list = MatcherTableWidget()
        self.match_list.setShowGrid(True)
        self.match_list.setAutoScroll(True)
        self.match_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.match_list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.match_list.itemSelectionChanged.connect(self.match_list_item_selection_changed)
        self.match_list.itemDoubleClicked.connect(self.match_list_double_clicked)
        self.match_list.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.preview = QLabel()
        self.preview_widget = QSplitter()
        self.preview_widget.setContentsMargins(0, 0, 0, 0)
        self.preview_widget.addWidget(self.match_list)
        self.preview_widget.addWidget(self.preview)

        # review settings
        settings = QSettings('WheresTheFckReceipt', 'WheresTheFckReceipt')
        self.query.setText(settings.value("query_text", ""))
        self.limit_box.setValue(settings.value("limit_box_value", 0))
        self.cs_box.setChecked(bool(settings.value("cs_box_checked", False)))

        # my layout
        layout = QVBoxLayout()
        layout.addLayout(query_bar_layout)
        layout.addWidget(self.preview_widget)
        self.setLayout(layout)

    def match_list_double_clicked(self, mi):
        row = mi.row()
        result = self.results[row]
        self.open_file(result.get_path())

    def open_file(self, filepath):
        if platform.system() == 'Darwin':  # macOS
            subprocess.call(('open', filepath))
        elif platform.system() == 'Windows':  # Windows
            os.startfile(filepath)
        else:  # linux variants
            subprocess.call(('xdg-open', filepath))

    def match_list_item_selection_changed(self):
        selected_items = self.match_list.selectedItems()
        if len(selected_items) == 0:
            return
        curr_row = self.match_list.currentRow()
        result = self.results[curr_row]
        im = result.get_preview_image()
        if im is not None:
            self.current_preview_image = QtGui.QImage(im.data, im.shape[1], im.shape[0], im.strides[0],
                                                      QtGui.QImage.Format_RGB888).rgbSwapped()
            w = self.preview_widget.width() / 2.0
            h = self.preview_widget.height()
            self.preview_widget.setSizes([w, w])

            self.preview.setPixmap(QPixmap(self.current_preview_image).scaled(w, h, Qt.KeepAspectRatio))
        else:
            self.preview.setText("Preview could not be loaded.")

    def splitter_moved(self, pos, index):
        w = self.preview.width()
        h = self.preview.height()
        self.preview.setPixmap(QPixmap(self.current_preview_image).scaled(w, h, Qt.KeepAspectRatio))

    def search_button_clicked(self):
        self.preview.setText("No image selected.")

        settings = QSettings('WheresTheFckReceipt', 'WheresTheFckReceipt')
        settings.setValue("query_text", self.query.text())
        settings.setValue("limit_box_value", self.limit_box.value())
        settings.setValue("cs_box_checked", self.cs_box.isChecked())
        del settings

        self.results = self.wheres_the_fck_receipt.search(self.query.text(), self.limit_box.value(),
                                                          self.cs_box.isChecked())
        self.match_list.clear()
        self.match_list.setColumnCount(3)
        self.match_list.setHorizontalHeaderLabels(['File', 'Page', 'Path'])
        header = self.match_list.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        # header.setStretchLastSection(True)
        self.match_list.setRowCount(len(self.results))
        for i in range(len(self.results)):
            result = self.results[i]
            path = result.get_path()
            self.match_list.setItem(i, 0, QTableWidgetItem(os.path.basename(path)))
            self.match_list.setItem(i, 1, QTableWidgetItem(str(result.get_page())))
            self.match_list.setItem(i, 2, QTableWidgetItem(os.path.dirname(path)))

        self.query.setFocus()
        self.query.selectAll()


class SettingsWidget(QTableWidget):
    def __init__(self, wheres_the_fck_receipt: api_interface.WheresTheFckReceipt, parent=None):
        QTableWidget.__init__(self, parent)

        self.wheres_the_fck_receipt = wheres_the_fck_receipt
        # settings table
        self.setShowGrid(True)
        # self.setSelectionMode(QAbstractItemView.SingleSelection)
        # self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(['Key', 'Value', 'Help'])
        header = self.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)

        row = 0
        settings = wheres_the_fck_receipt.get_settings()
        for key, value_help_type in settings.items():
            key_item = QTableWidgetItem(key)
            key_item.setFlags(key_item.flags() ^ Qt.ItemIsEditable)

            value_ = value_help_type[0]
            type_ = value_help_type[2]
            value_item = QTableWidgetItem(value_)
            value_item.setData(Qt.UserRole, type_)
            # value_item.setFlags(value_item.flags() & Qt.ItemIsEditable)

            help_ = value_help_type[1]
            # help_item = QTableWidgetItem(help_)
            # help_item.setFlags(help_item.flags() ^ Qt.ItemIsEditable)
            help_item = QLabel(help_)
            help_item.setTextFormat(Qt.RichText)
            help_item.setOpenExternalLinks(True)

            self.insertRow(row)
            self.setItem(row, 0, key_item)
            self.setItem(row, 1, value_item)
            # self.setItem(row, 2, help_item)
            self.setCellWidget(row, 2, help_item)
            row = row + 1

        self.cellChanged.connect(self.on_cell_changed)

    def on_cell_changed(self):
        settings = {}
        for row in range(self.rowCount()):
            # item(row, 0) Returns the item for the given row and column if one has been set; otherwise returns nullptr.
            key_ = self.item(row, 0).text()
            value_ = self.item(row, 1).text()
            settings[key_] = value_
        self.wheres_the_fck_receipt.set_settings(settings)


class WheresTheFckReceipt(QMainWindow):

    def __init__(self, wheres_the_fck_receipt: api_interface.WheresTheFckReceipt, parent=None):
        QWidget.__init__(self, parent=None)
        self.wheres_the_fck_receipt = wheres_the_fck_receipt

        # tab widget
        self.tab_widget = QTabWidget()
        self.tab_widget.addTab(SettingsWidget(wheres_the_fck_receipt), "Settings")
        self.tab_widget.addTab(Indexer(wheres_the_fck_receipt), "Indexer")
        self.tab_widget.addTab(SearcherWidget(wheres_the_fck_receipt), "Searcher")
        self.tab_widget.currentChanged.connect(self.tab_changed)

        # get settings
        settings = QSettings('WheresTheFckReceipt', 'WheresTheFckReceipt')
        active_tab = settings.value("active_tab", 0)
        self.tab_widget.setCurrentIndex(active_tab)

        # build window title
        app_context = ApplicationContext()
        version = app_context.build_settings['version']
        app_name = app_context.build_settings['app_name']
        window_title = app_name + " v" + version

        # build main window
        self.setWindowTitle(window_title)
        self.setCentralWidget(self.tab_widget)
        self.resize(800, 600)

    def tab_changed(self, index):
        settings = QSettings('WheresTheFckReceipt', 'WheresTheFckReceipt')
        settings.setValue("active_tab", index)
        del settings
