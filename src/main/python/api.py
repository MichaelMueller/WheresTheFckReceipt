import hashlib
import os
import random
import sqlite3
import string
import sys
import threading
import time
from typing import List, Dict, Tuple

import cv2
import numpy as np
# import pd2image_patched
from pd2image_patched import convert_from_path
from pytesseract import pytesseract, Output

import api_interface


class IndexJob(api_interface.IndexJob):

    def __init__(self, path, db_factory: api_interface.DbFactory, app_data_path, poppler_path=None, tesseract_exe=None):
        self.path = path
        self.db_factory = db_factory
        self.app_data_path = app_data_path
        self.poppler_path = poppler_path
        self.tesseract_exe = tesseract_exe

        self._stop = False
        self.curr_file_idx = None
        self.num_files = None
        self.status = None  # type: str
        self.finished = False

        self.__messages_mutex = threading.Lock()
        self.__messages = None  # type: List[str]

    def start(self):
        # init vars
        self._stop = False
        self.curr_file_idx = None
        self.num_files = None
        self.__messages = []  # type: List[str]
        self.finished = False
        # start thread
        thread = threading.Thread(target=self.run, args=())
        thread.daemon = True  # Daemonize thread
        thread.start()  # Start the execution

    def stop(self):
        self._stop = True

    def get_path(self) -> str:
        return self.path

    def get_curr_file_index(self) -> int:
        return self.curr_file_idx

    def get_num_files(self) -> str:
        return self.num_files

    def get_messages(self) -> List[str]:
        messages = []
        with self.__messages_mutex:
            for message in self.__messages:
                messages.append(message)
            self.__messages.clear()
        return messages

    def is_finished(self) -> bool:
        return self.finished

    def __add_message(self, msg):
        with self.__messages_mutex:
            self.__messages.append(msg)

    def __get_files(self):
        scan_files = []
        for root, dirs, files in os.walk(self.path):
            for basename in files:
                if self._stop:
                    return []
                file_name, ext = os.path.splitext(basename)
                ext = ext[1:].lower()
                if ext not in ["jpg", "jpeg", "png", "bmp", "pdf"]:
                    continue
                path = os.path.join(root, basename).replace("\\", "/")
                scan_files.append(path)
        return scan_files

    def __process_image_file(self, c: sqlite3.Cursor, path, dir_id, doc_path, page):

        if c.execute("select id from images where path = ?", (path,)).fetchone() is not None:
            self.__add_message("Skipping already indexed file {}.".format(path.replace(self.path + "/", "")))
            return

        img = cv2.imread(path)
        img_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(img_gray, (9, 9), 0)
        img = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
        # img = 255 - thresh
        # cv2.imwrite(path.replace(self.path, "C:/Users/mueller/Desktop/output"), img)
        # img = remove_noise.process_image_for_ocr(path)
        if self.tesseract_exe:
            pytesseract.tesseract_cmd = self.tesseract_exe
        d = pytesseract.image_to_data(img, output_type=Output.DICT)

        c.execute("insert into 'images' (path, directory_id) values (?, ?)", (path, dir_id))
        image_id = c.lastrowid
        for j in range(len(d["text"])):
            if not d['text'][j].strip():
                continue
            c.execute(
                "insert into 'texts' (text, left, top, width, height, image_id) values (?, ?, ?, ?, ?, ?)",
                (d['text'][j], d['left'][j], d['top'][j], d['width'][j], d['height'][j], image_id))

        if doc_path is not None and page is not None:
            doc_id = c.execute("select id from documents where path = ?", (doc_path,)).fetchone()
            if doc_id is None:
                c.execute("insert into documents (path, directory_id) values (?, ?)", (doc_path, dir_id))
                doc_id = c.lastrowid
            else:
                doc_id = doc_id[0]
            c.execute("update images set document_id = ?, doc_page = ? where id = ?", (doc_id, page, image_id))

    def run(self):
        try:
            # get dir id
            db = self.db_factory.create()
            c = db.cursor()
            res = c.execute("select id from directories where path = ?", (self.path,)).fetchone()
            if res is not None:
                dir_id = res[0]
            else:
                c.execute("insert into 'directories' (path) values (?)", (self.path,))
                dir_id = c.lastrowid
                db.commit()

            # collect files
            self.__add_message("Scanning files in {}".format(self.path))
            scan_files = self.__get_files()
            self.num_files = len(scan_files)
            self.__add_message("Scanning files finished. Found {} files for indexing.".format(self.num_files))

            # process files
            for i in range(self.num_files):
                if self._stop:
                    break

                self.curr_file_idx = i
                path = scan_files[i]
                rel_path = path.replace(self.path + "/", "")

                self.__add_message(
                    "File {} of {}: Analyzing {}.".format(i + 1, self.num_files, rel_path))

                try:
                    _, ext = os.path.splitext(path)
                    image_paths = []
                    if ext.lower() == ".pdf":

                        if c.execute("select id from documents where path = ?", (path,)).fetchone() is not None:
                            self.__add_message(
                                "Skipping already indexed file {}.".format(path.replace(self.path + "/", "")))
                            continue

                        self.__add_message(
                            "Converting {} to single image files.".format(rel_path))
                        if self.poppler_path:
                            images = convert_from_path(path, 300, poppler_path=self.poppler_path)
                        else:
                            images = convert_from_path(path, 300)
                        page = 0
                        for image in images:
                            page = page + 1
                            # while True:
                            #    img_path = self.app_data_path + "/" + self.random_string() + ".png"
                            #    if not os.path.exists(img_path):
                            #        break
                            img_path = self.app_data_path + "/" + hashlib.md5(
                                path.encode('utf-8')).hexdigest() + "_page" + str(
                                page) + ".jpg"
                            self.__add_message(
                                "Writing page {} of {} as image {}.".format(page, len(images), img_path))
                            # cv2.imwrite(img_path, image)
                            if not os.path.exists(img_path):
                                image.save(img_path, 'JPEG')
                            image_paths.append((img_path, path, page))
                    else:
                        image_paths = [(path, None, None)]

                    for img_path in image_paths:
                        self.__add_message(
                            "Extracting text from {}.".format(
                                img_path[0].replace(self.path + "/", "")))
                        try:
                            self.__process_image_file(c, img_path[0], dir_id, img_path[1], img_path[2])
                        except AttributeError as e:
                            self.__add_message("Attribute error occured while converting {}: {}".format(
                                img_path[0].replace(self.path + "/", ""), str(e.__str__())))
                        except Exception as e:
                            self.__add_message("Exception occured while converting {}: {}".format(
                                img_path[0].replace(self.path + "/", ""), str(e.__str__())))
                        except:
                            self.__add_message("An unknown error occured while converting {}: {}".format(
                                img_path[0].replace(self.path + "/", ""), str(sys.exc_info()[0])))

                    db.commit()
                except:
                    self.__add_message("An unknown error occured while processing {}: {}".format(
                        rel_path, sys.exc_info()[0]))

            # commit or rollback
            if self._stop:
                self.__add_message("Indexing stopped")
                db.rollback()
            else:
                self.__add_message("Indexing successfully finished")
                db.commit()
            self.finished = True
        except:
            self._stop = True
            e = sys.exc_info()[0]
            self.__add_message("An unknown error occured: " + str(e))
            db.rollback()

    def random_string(self, stringLength=5):
        letters = string.ascii_lowercase
        return ''.join(random.choice(letters) for i in range(stringLength))


class TextMatch(api_interface.TextMatch):

    def __init__(self, text, top, left, width, height):
        self.text = text
        self.top = top
        self.left = left
        self.width = width
        self.height = height

    def get_text(self) -> str:
        return self.text


class Result(api_interface.Result):

    def __init__(self, path, page, doc_path, text_matches: List[TextMatch], image_id):
        self.path = path
        self.page = page
        self.doc_path = doc_path
        self.text_matches = text_matches
        self.image_id = image_id

    def get_path(self) -> str:
        return self.doc_path if self.doc_path is not None else self.path

    def get_text_matches(self) -> str:
        return self.text_matches

    def get_page(self) -> int:
        return self.page

    def get_preview_image(self) -> np.ndarray:
        preview_image = None

        if not os.path.exists(self.path):
            return preview_image
        image = cv2.imread(self.path)
        overlay = image.copy()
        preview_image = image
        color_per_text = {}
        for text_match in self.text_matches:
            x, y, w, h = text_match.left, text_match.top, text_match.width, text_match.height  # Rectangle parameters
            text = text_match.get_text()
            if text not in color_per_text.keys():
                color_per_text[text] = tuple(list(np.random.choice(range(128, 255), size=3)))
            color = color_per_text[text]
            cv2.rectangle(overlay, (x, y), (x + w, y + h), (0,0,255), 5)  # A filled rectangle

            alpha = 0.65  # Transparency factor.

            # Following line overlays transparent rectangle over the image
            preview_image = cv2.addWeighted(overlay, alpha, preview_image, 1 - alpha, 0)

        return preview_image


class WheresTheFckReceipt(api_interface.WheresTheFckReceipt):

    def __init__(self, app_data_dir, db_factory: api_interface.DbFactory,
                 index_job_factory: api_interface.IndexJobFactory):
        self.app_data_dir = app_data_dir
        self.db_factory = db_factory
        self.index_job_factory = index_job_factory
        self.db = None

    def get_last_directory(self) -> str:
        self.assert_db()
        c = self.db.cursor()
        c.execute("select path from directories order by id desc limit 1")
        row = c.fetchone()
        return row[0] if row and os.path.exists(row[0]) else None

    def assert_db(self):
        if not self.db:
            self.db = self.db_factory.create()

    def get_directories(self) -> List[str]:
        self.assert_db()
        c = self.db.cursor()
        c.execute("select path from directories")
        rows = c.fetchall()
        return [i[0] for i in rows]

    def add_directory(self, directory) -> IndexJob:
        poppler_path = self.get_setting("poppler_path")
        tesseract_exe = self.get_setting("tesseract_exe")
        return self.index_job_factory.create(directory, self.db_factory, self.app_data_dir, poppler_path, tesseract_exe)

    def remove_directory(self, directory):
        self.assert_db()
        c = self.db.cursor()
        own_images = c.execute(
            "select images.path as path from images, directories where directories.id = images.directory_id and images.document_id != null")
        for own_image in own_images.fetchall():
            os.remove(own_image[0])
        c.execute("delete from directories where path = ?", (directory,))
        self.db.commit()

    def update_directory(self, directory):
        return self.add_directory(directory)

    def reindex_directory(self, directory) -> IndexJob:
        self.remove_directory(directory)
        return self.add_directory(directory)

    def search(self, query: str, limit: int = None, case_sensitive: bool = False) -> List[Result]:
        self.assert_db()
        c = self.db.cursor()
        if case_sensitive:
            text_where_clause = "texts.text like ?"
        else:
            text_where_clause = "lower(texts.text) like ?"

        query_parts = query.split()
        result_list = []
        final_result_list = []
        previous_ids = []
        for query_part_idx, query_part in enumerate(query_parts):
            args = []
            sql = "select images.path as path, texts.text as text, images.doc_page as page, " \
                  "images.document_id as doc_id, texts.top as top, texts.left as left, texts.width as width, " \
                  "texts.height as height, images.id as image_id"
            sql = sql + " from images, texts"
            sql = sql + " where texts.image_id = images.id and " + text_where_clause

            if case_sensitive:
                query_part = "%" + query_part.lower() + "%"
            else:
                query_part = "%" + query_part + "%"
            args.append(query_part)

            if len(previous_ids) > 0:
                sql = sql + "and ("
                for idx, previous_id in enumerate(previous_ids):
                    sql = sql + " images.id = ?"
                    args.append(previous_id)
                    if idx + 1 < len(previous_ids):
                        sql = sql + " or"
                sql = sql + " )"

            if limit:
                sql = sql + " limit ?"
                args.append(limit)

            c.execute(sql, tuple(args))
            rows = c.fetchall()
            for row in rows:
                image_path = row[0]
                text = row[1]
                page = row[2]
                doc_id = row[3]
                top = row[4]
                left = row[5]
                width = row[6]
                height = row[7]
                image_id = row[8]

                if image_id not in previous_ids:
                    previous_ids.append(image_id)

                doc_path = c.execute("select path from documents where id = ?", (doc_id,)).fetchone()[
                    0] if doc_id else None

                text_match = TextMatch(text, top, left, width, height)
                result = None
                for curr_result in result_list:
                    if curr_result.path == image_path and curr_result.page == page:
                        result = curr_result
                        break
                if result is None:
                    text_matches = [text_match]
                    result = Result(image_path, page, doc_path, text_matches, image_id)
                    result_list.append(result)
                else:
                    result.text_matches.append(text_match)

                if query_part_idx + 1 == len(query_parts):
                    final_result_list.append(result)

        return final_result_list

    def get_setting(self, key):
        settings = self.get_settings()
        if key in settings.keys():
            value_ = settings[key][0]
            type_ = settings[key][2]
            if type_ == "int":
                value_ = int(value_)
            elif value_ == "":
                return None
            return value_
        return None

    def get_settings(self) -> Dict[str, Tuple[str, str, str]]:
        self.assert_db()
        c = self.db.cursor()
        c.execute("select key, value, help, type from settings where hidden != 1")
        rows = c.fetchall()
        settings = {}
        settings_value = {}
        for row in rows:
            settings[row[0]] = (row[1], row[2], row[3])
            settings_value[row[0]] = row[1]
        return settings

    def set_settings(self, settings: Dict[str, str]):
        self.assert_db()
        c = self.db.cursor()
        existing_settings = self.get_settings().keys()
        for key, value in settings.items():
            if key in existing_settings:
                c.execute("update settings set value=? where key = ?", (value, key))
            else:
                c.execute("insert into settings (key, value) values(?, ?)", (key, value))
        self.db.commit()


class IndexJobFactory(api_interface.IndexJobFactory):

    def create(self, path, db_factory: api_interface.DbFactory, app_data_dir, poppler_path=None,
               tesseract_exe=None) -> IndexJob:
        return IndexJob(path, db_factory, app_data_dir, poppler_path, tesseract_exe)


class DbFactory(api_interface.DbFactory):
    def __init__(self, app_data_dir: str, delete_db=False):
        self.app_data_dir = app_data_dir
        self.db_path = app_data_dir + "/db.sqlite3"
        self.delete_db = delete_db

    def create(self) -> sqlite3.Connection:
        if self.delete_db and os.path.exists(self.db_path):
            os.remove(self.db_path)
        # database
        db_path = self.db_path
        if not os.path.exists(os.path.dirname(db_path)):
            os.makedirs(os.path.dirname(db_path))
        create_database = not os.path.exists(db_path)

        db = sqlite3.connect(db_path)
        c = db.cursor()
        c.execute("PRAGMA foreign_keys = ON")
        if create_database:
            c.execute(
                "create table settings (key text primary key, value text, help text, type text not null, hidden integer not null)")
        self.update_schema(c)
        db.commit()
        return db

    def update_schema(self, c: sqlite3.Cursor):

        current_schema_version = None
        rows = c.execute("select value from settings where key = 'current_schema_version'").fetchone()
        if rows:
            current_schema_version = int(rows[0])

        if current_schema_version is None:
            c.execute("CREATE TABLE directories ( id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE )")
            c.execute(
                "CREATE TABLE documents ( id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE NOT NULL, directory_id INTEGER NOT NULL, FOREIGN KEY(directory_id) REFERENCES directories(id) ON DELETE CASCADE )")
            c.execute(
                "CREATE TABLE images ( id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE NOT NULL, directory_id INTEGER NOT NULL, document_id INTEGER, doc_page INTEGER, FOREIGN KEY(directory_id) REFERENCES directories(id) ON DELETE CASCADE, FOREIGN KEY(document_id) REFERENCES documents(id) )")
            c.execute(
                "CREATE TABLE texts ( id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, left INTEGER  NOT NULL, top INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, image_id INTEGER NOT NULL, FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE )")
            c.execute(
                "insert into settings (key, value, help, type, hidden) values('current_schema_version',  '1', 'Current Schema Version', 'int', 1)")
            c.execute(
                "insert into settings (key, value, help, type, hidden) values('tesseract_exe', null, 'Path to Tesseract Exe', 'file', 0)")
            c.execute(
                "insert into settings (key, value, help, type, hidden) values('pdftopmm_exe', null, 'Path to Poppler Exe', 'file', 0)")

        current_schema_version = 0 if current_schema_version is None else current_schema_version
        if current_schema_version <= 1:
            c.execute(
                "insert into settings (key, value, help, type, hidden) values('default_limit', 0, 'The default limit for the search', 'int', 0)")

        if current_schema_version <= 2:
            c.execute(
                "insert into settings (key, value, help, type, hidden) values('poppler_path', 0, 'The path for poppler', 'dir', 0)")
            c.execute("delete from settings where key = 'pdftopmm_exe'")

        if current_schema_version <= 3:
            c.execute("update settings set value=NULL where key = 'poppler_path'")

        if current_schema_version <= 4:
            c.execute(
                "update settings set help='Path to the poppler bin directory. Leave this empty if the path is in your PATH variable. Poppler for Windows can be downloaded from https://blog.alivate.com.au/poppler-windows/' where key = 'poppler_path'")
            c.execute(
                "update settings set help='Path to Tesseract executable. Leave this empty if the executable is in your PATH variable. Tesseract for Windows can be downloaded from https://github.com/UB-Mannheim/tesseract/wiki' where key = 'tesseract_exe'")

        if current_schema_version <= 5:
            c.execute(
                "update settings set help='Path to the poppler bin directory. Leave this empty if the path is in your PATH variable. Poppler for Windows can be downloaded from <a href=\"https://blog.alivate.com.au/poppler-windows/\">https://blog.alivate.com.au/poppler-windows/</a>' where key = 'poppler_path'")
            c.execute(
                "update settings set help='Path to Tesseract executable. Leave this empty if the executable is in your PATH variable. Tesseract for Windows can be downloaded from <a href=\"https://github.com/UB-Mannheim/tesseract/wiki\">https://github.com/UB-Mannheim/tesseract/wiki</a>' where key = 'tesseract_exe'")

        c.execute("update settings set value=? where key = 'current_schema_version'", (5,))
