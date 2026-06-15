#!/usr/bin/env python3
import cgi
import json
import mimetypes
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import extract_exif


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("PHOTO_WALL_DATA_DIR", ROOT)).resolve()
GROUPS_FILE = DATA_DIR / "groups.json"
DB_FILE = DATA_DIR / "photo_wall.db"
UPLOADS_DIR = DATA_DIR / "uploads"
LEGACY_PHOTOS_DIR = DATA_DIR / "photos"
LEGACY_DATA_FILE = DATA_DIR / "photos.json"
REVALIDATE_STATIC_EXTS = {".css", ".js"}
LONG_CACHE_STATIC_EXTS = {".png", ".ico", ".svg", ".woff2"}


def static_cache_control(path):
    clean = path.split("?")[0]
    _, ext = os.path.splitext(clean)
    ext = ext.lower()
    if ext in REVALIDATE_STATIC_EXTS:
        return "no-cache"
    if ext in LONG_CACHE_STATIC_EXTS:
        return "public, max-age=86400"
    return None


IMAGE_EXTS = extract_exif.IMAGE_EXTS
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_FILES = 2000
DATA_LOCK = threading.Lock()
DEFAULT_ADMIN_PATH = "/admin"
APP_VERSION = os.environ.get("PHOTO_WALL_VERSION", "dev")
UNCHANGED = object()
extract_exif.configure_paths(DATA_DIR)


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def slug_id():
    return uuid.uuid4().hex[:12]


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path, payload):
    temp = Path(str(path) + ".tmp")
    with open(temp, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    os.replace(temp, path)


def connect_db():
    connection = sqlite3.connect(DB_FILE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_db():
    with connect_db() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS groups (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                cover_photo_id TEXT,
                story_cover_photo_id TEXT,
                show_cover_in_details INTEGER NOT NULL DEFAULT 0,
                show_story_cover_in_details INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS photos (
                id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                data TEXT NOT NULL,
                FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_photos_group_order
                ON photos(group_id, sort_order);
            """
        )


def has_db_groups():
    with connect_db() as connection:
        row = connection.execute("SELECT COUNT(*) AS total FROM groups").fetchone()
    return bool(row["total"])


def normalize_admin_path(path):
    value = str(path or "").strip()
    if not value:
        raise ValueError("请输入管理入口路径")
    if not value.startswith("/"):
        value = "/" + value
    value = value.rstrip("/") or "/"
    if value == "/":
        raise ValueError("管理入口不能是根路径")
    if any(char.isspace() for char in value):
        raise ValueError("管理入口不能包含空格")
    if "\\" in value or "?" in value or "#" in value or ".." in value:
        raise ValueError("管理入口包含不支持的字符")
    reserved = (
        "/api",
        "/uploads",
        "/photos",
        "/admin.html",
        "/index.html",
        "/script.js",
        "/style.css",
        "/admin.js",
        "/admin.css",
    )
    if value == reserved[0] or any(value.startswith(item + "/") or value == item for item in reserved):
        raise ValueError("管理入口与系统路径冲突")
    return value


def default_admin_path():
    return normalize_admin_path(os.environ.get("PHOTO_WALL_ADMIN_PATH", DEFAULT_ADMIN_PATH))


def get_setting(key, default=None):
    with connect_db() as connection:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()
    return row["value"] if row and row["value"] is not None else default


def set_setting(key, value):
    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def get_admin_path():
    try:
        return normalize_admin_path(get_setting("admin_path", default_admin_path()))
    except ValueError:
        return DEFAULT_ADMIN_PATH


def load_store():
    with connect_db() as connection:
        active_group_id = connection.execute(
            "SELECT value FROM settings WHERE key = 'active_group_id'"
        ).fetchone()
        groups = []
        group_rows = connection.execute(
            """
            SELECT *
            FROM groups
            ORDER BY sort_order, created_at, id
            """
        ).fetchall()
        for group_row in group_rows:
            photos = []
            photo_rows = connection.execute(
                """
                SELECT data
                FROM photos
                WHERE group_id = ?
                ORDER BY sort_order, id
                """,
                (group_row["id"],),
            ).fetchall()
            for photo_row in photo_rows:
                try:
                    photos.append(json.loads(photo_row["data"]))
                except json.JSONDecodeError:
                    continue
            groups.append(
                {
                    "id": group_row["id"],
                    "name": group_row["name"],
                    "created_at": group_row["created_at"],
                    "updated_at": group_row["updated_at"],
                    "photos": photos,
                    "cover_photo_id": group_row["cover_photo_id"],
                    "story_cover_photo_id": group_row["story_cover_photo_id"],
                    "show_cover_in_details": bool(group_row["show_cover_in_details"]),
                    "show_story_cover_in_details": bool(
                        group_row["show_story_cover_in_details"]
                    ),
                }
            )
    return {
        "active_group_id": active_group_id["value"] if active_group_id else None,
        "groups": groups,
    }


def save_store(store):
    with connect_db() as connection:
        settings = {
            row["key"]: row["value"]
            for row in connection.execute("SELECT key, value FROM settings").fetchall()
        }
        settings["active_group_id"] = store.get("active_group_id")
        connection.execute("DELETE FROM photos")
        connection.execute("DELETE FROM groups")
        connection.execute("DELETE FROM settings")
        for key, value in settings.items():
            if value is not None:
                connection.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)",
                    (key, value),
                )
        for group_index, group in enumerate(store.get("groups", [])):
            connection.execute(
                """
                INSERT INTO groups (
                    id,
                    name,
                    created_at,
                    updated_at,
                    cover_photo_id,
                    story_cover_photo_id,
                    show_cover_in_details,
                    show_story_cover_in_details,
                    sort_order
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group["id"],
                    group["name"],
                    group["created_at"],
                    group["updated_at"],
                    group.get("cover_photo_id"),
                    group.get("story_cover_photo_id"),
                    int(bool(group.get("show_cover_in_details", False))),
                    int(bool(group.get("show_story_cover_in_details", False))),
                    group_index,
                ),
            )
            for photo_index, photo in enumerate(group.get("photos", [])):
                connection.execute(
                    """
                    INSERT INTO photos (id, group_id, sort_order, data)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        photo["id"],
                        group["id"],
                        photo_index,
                        json.dumps(photo, ensure_ascii=False, separators=(",", ":")),
                    ),
                )


def upsert_group(connection, group, sort_order):
    connection.execute(
        """
        INSERT INTO groups (
            id, name, created_at, updated_at,
            cover_photo_id, story_cover_photo_id,
            show_cover_in_details, show_story_cover_in_details,
            sort_order
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            updated_at = excluded.updated_at,
            cover_photo_id = excluded.cover_photo_id,
            story_cover_photo_id = excluded.story_cover_photo_id,
            show_cover_in_details = excluded.show_cover_in_details,
            show_story_cover_in_details = excluded.show_story_cover_in_details,
            sort_order = excluded.sort_order
        """,
        (
            group["id"],
            group["name"],
            group["created_at"],
            group["updated_at"],
            group.get("cover_photo_id"),
            group.get("story_cover_photo_id"),
            int(bool(group.get("show_cover_in_details", False))),
            int(bool(group.get("show_story_cover_in_details", False))),
            sort_order,
        ),
    )


def replace_group_photos(connection, group):
    connection.execute("DELETE FROM photos WHERE group_id = ?", (group["id"],))
    for photo_index, photo in enumerate(group.get("photos", [])):
        connection.execute(
            """
            INSERT INTO photos (id, group_id, sort_order, data)
            VALUES (?, ?, ?, ?)
            """,
            (
                photo["id"],
                group["id"],
                photo_index,
                json.dumps(photo, ensure_ascii=False, separators=(",", ":")),
            ),
        )


def write_active_group_id(connection, group_id):
    if group_id is None:
        connection.execute("DELETE FROM settings WHERE key = 'active_group_id'")
        return
    connection.execute(
        """
        INSERT INTO settings (key, value)
        VALUES ('active_group_id', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (group_id,),
    )


def save_group(
    group,
    sort_order,
    replace_photos=False,
    active_group_id=UNCHANGED,
):
    with connect_db() as connection:
        upsert_group(connection, group, sort_order)
        if replace_photos:
            replace_group_photos(connection, group)
        if active_group_id is not UNCHANGED:
            write_active_group_id(connection, active_group_id)


def delete_group_record(group_id, active_group_id=UNCHANGED):
    with connect_db() as connection:
        connection.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        if active_group_id is not UNCHANGED:
            write_active_group_id(connection, active_group_id)


def set_active_group_id(group_id):
    with connect_db() as connection:
        write_active_group_id(connection, group_id)


def ensure_store_schema(store):
    changed = False
    for group in store.get("groups", []):
        photos = group.setdefault("photos", [])
        for photo in photos:
            if not photo.get("id"):
                photo["id"] = uuid.uuid4().hex
                changed = True
        if "cover_photo_id" not in group:
            group["cover_photo_id"] = photos[0]["id"] if photos else None
            changed = True
        if "story_cover_photo_id" not in group:
            group["story_cover_photo_id"] = (
                photos[1]["id"] if len(photos) > 1 else (photos[0]["id"] if photos else None)
            )
            changed = True
        if "show_cover_in_details" not in group:
            group["show_cover_in_details"] = False
            changed = True
        if "show_story_cover_in_details" not in group:
            group["show_story_cover_in_details"] = False
            changed = True
        if (
            group.get("cover_photo_id")
            and group.get("cover_photo_id") == group.get("story_cover_photo_id")
        ):
            unified_visibility = bool(
                group.get("show_cover_in_details")
                or group.get("show_story_cover_in_details")
            )
            if (
                group.get("show_cover_in_details") != unified_visibility
                or group.get("show_story_cover_in_details") != unified_visibility
            ):
                group["show_cover_in_details"] = unified_visibility
                group["show_story_cover_in_details"] = unified_visibility
                changed = True
    return store, changed


def initialize_store():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(exist_ok=True)
    initialize_db()
    if not get_setting("admin_path"):
        set_setting("admin_path", default_admin_path())
    if has_db_groups():
        with DATA_LOCK:
            store, changed = ensure_store_schema(load_store())
            if changed:
                save_store(store)
        return
    if GROUPS_FILE.exists():
        store = read_json(GROUPS_FILE, {"active_group_id": None, "groups": []})
    else:
        legacy_photos = read_json(LEGACY_DATA_FILE, [])
        group_id = slug_id()
        group = {
            "id": group_id,
            "name": "默认相册",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "photos": legacy_photos,
            "cover_photo_id": None,
            "story_cover_photo_id": None,
            "show_cover_in_details": False,
            "show_story_cover_in_details": False,
        }
        store = {"active_group_id": group_id, "groups": [group]}
    store, _ = ensure_store_schema(store)
    save_store(store)


def find_group(store, group_id):
    return next((group for group in store["groups"] if group["id"] == group_id), None)


def remove_managed_file(store, photo):
    relative_path = str(photo.get("path", "")).replace("\\", "/")
    if any(
        item.get("path") == relative_path
        for group in store.get("groups", [])
        for item in group.get("photos", [])
    ):
        return
    target = resolve_public_file(relative_path)
    if not target:
        return
    managed_roots = (UPLOADS_DIR.resolve(), LEGACY_PHOTOS_DIR.resolve())
    if any(target == root or root in target.parents for root in managed_roots):
        target.unlink(missing_ok=True)


def resolve_public_file(relative_path):
    relative_path = unquote(str(relative_path).replace("\\", "/")).lstrip("/")
    if relative_path.startswith(("uploads/", "photos/")):
        base = DATA_DIR
    else:
        base = ROOT
    target = (base / relative_path).resolve()
    allowed_roots = (ROOT.resolve(), UPLOADS_DIR.resolve(), LEGACY_PHOTOS_DIR.resolve())
    if any(target == root or root in target.parents for root in allowed_roots):
        return target
    return None


def public_store(store):
    store, _ = ensure_store_schema(store)
    return {
        "active_group_id": store.get("active_group_id"),
        "groups": [
            {
                "id": group["id"],
                "name": group["name"],
                "created_at": group["created_at"],
                "updated_at": group["updated_at"],
                "photo_count": len(group.get("photos", [])),
                "cover_photo_id": group.get("cover_photo_id"),
                "story_cover_photo_id": group.get("story_cover_photo_id"),
                "show_cover_in_details": group.get("show_cover_in_details", False),
                "show_story_cover_in_details": group.get("show_story_cover_in_details", False),
                "cover": next(
                    (
                        photo["path"]
                        for photo in group.get("photos", [])
                        if photo["id"] == group.get("cover_photo_id")
                    ),
                    group["photos"][0]["path"] if group.get("photos") else None,
                ),
            }
            for group in store["groups"]
        ],
    }


def safe_filename(name):
    extension = Path(name).suffix.lower()
    return f"{uuid.uuid4().hex}{extension}"


def extract_photo_data(filepath, public_path, original_name, resolve_locations=False):
    exif = extract_exif.extract(str(filepath))
    photo = {
        "id": uuid.uuid4().hex,
        "name": original_name,
        "stored_name": filepath.name,
        "path": public_path.replace("\\", "/"),
        "uploaded_at": now_iso(),
    }

    date_raw = (
        exif.get("DateTimeOriginal")
        or exif.get("DateTime")
        or exif.get("DateTimeDigitized")
    )
    if date_raw:
        photo["date"] = extract_exif.format_date(date_raw)
        photo["date_raw"] = date_raw

    camera = extract_exif.format_camera(
        exif.get("camera_make"), exif.get("camera_model")
    )
    if camera:
        photo["camera"] = camera

    lat = exif.get("latitude")
    lon = exif.get("longitude")
    if lat is not None and lon is not None:
        photo["latitude"] = lat
        photo["longitude"] = lon
        cache = extract_exif.load_location_cache()
        key = extract_exif.location_key(lat, lon)
        location = cache.get(key)
        if not location and resolve_locations:
            try:
                location = extract_exif.reverse_geocode(lat, lon)
                if location:
                    cache[key] = location
                    extract_exif.save_location_cache(cache)
                time.sleep(1.1)
            except Exception as error:
                photo["location_error"] = str(error)
        if location:
            photo["location"] = location

    return photo


def save_uploaded_image(file_item, group_id, resolve_locations):
    original_name = Path(file_item.filename or "photo").name
    extension = Path(original_name).suffix.lower()
    if extension not in IMAGE_EXTS:
        raise ValueError(f"不支持的图片格式: {original_name}")

    target_dir = UPLOADS_DIR / group_id
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_name = safe_filename(original_name)
    target = target_dir / stored_name
    with open(target, "wb") as output:
        shutil.copyfileobj(file_item.file, output)
    return extract_photo_data(
        target,
        f"uploads/{group_id}/{stored_name}",
        original_name,
        resolve_locations,
    )


def safe_extract_zip(zip_path, output_dir):
    extracted = []
    with zipfile.ZipFile(zip_path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > MAX_ZIP_FILES:
            raise ValueError(f"压缩包文件数量超过限制（{MAX_ZIP_FILES}）")

        for info in infos:
            source_name = Path(info.filename)
            if source_name.suffix.lower() not in IMAGE_EXTS:
                continue
            target = output_dir / safe_filename(source_name.name)
            with archive.open(info) as source, open(target, "wb") as destination:
                shutil.copyfileobj(source, destination)
            extracted.append((target, source_name.name))
    return extracted


class PhotoWallHandler(SimpleHTTPRequestHandler):
    server_version = f"PhotoWall/{APP_VERSION}"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        if not hasattr(self, "_cache_header_set"):
            cache_control = static_cache_control(self.path)
            self.send_header("Cache-Control", cache_control or "no-store")
        super().end_headers()

    def log_request(self, code="-", size="-"):
        if isinstance(code, int) and code < 400:
            return
        super().log_request(code, size)

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_error_json(self, message, status=HTTPStatus.BAD_REQUEST):
        self.send_json({"ok": False, "error": message}, status)

    def serve_data_file(self, request_path):
        target = resolve_public_file(request_path)
        if not target or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        try:
            stat = target.stat()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        etag = '"' + str(stat.st_mtime_ns) + '-' + str(stat.st_size) + '"'
        if_none_match = self.headers.get("If-None-Match")
        if if_none_match and if_none_match == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self._cache_header_set = True
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("ETag", etag)
            self.end_headers()
            return
        try:
            with open(target, "rb") as file:
                data = file.read()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._cache_header_set = True
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("ETag", etag)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        admin_path = get_admin_path()
        if path != admin_path and path.rstrip("/") == admin_path:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", admin_path)
            self.end_headers()
            return
        if path == admin_path:
            self.path = "/admin.html"
            super().do_GET()
            return
        if path == "/admin.html":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", admin_path)
            self.end_headers()
            return
        if path.startswith(("/uploads/", "/photos/")):
            self.serve_data_file(path)
            return
        if path == "/api/health":
            self.send_json({"ok": True, "version": APP_VERSION, "time": now_iso()})
            return
        if path == "/api/settings":
            self.send_json({"ok": True, "admin_path": admin_path})
            return
        if path == "/api/groups":
            self.send_json({"ok": True, **public_store(load_store())})
            return
        if path == "/api/active-photos":
            store, _ = ensure_store_schema(load_store())
            requested_group_id = parse_qs(parsed_url.query).get("group_id", [None])[0]
            group = find_group(
                store,
                requested_group_id or store.get("active_group_id"),
            )
            photos = list(group.get("photos", [])) if group else []
            cover_photo = None
            story_cover_photo = None
            if group:
                cover_photo = next(
                    (photo for photo in photos if photo["id"] == group.get("cover_photo_id")),
                    photos[0] if photos else None,
                )
                story_cover_photo = next(
                    (
                        photo
                        for photo in photos
                        if photo["id"] == group.get("story_cover_photo_id")
                    ),
                    photos[1] if len(photos) > 1 else cover_photo,
                )
                cover_roles = {}
                if cover_photo:
                    cover_roles.setdefault(cover_photo["id"], []).append(
                        group.get("show_cover_in_details", False)
                    )
                if story_cover_photo:
                    cover_roles.setdefault(story_cover_photo["id"], []).append(
                        group.get("show_story_cover_in_details", False)
                    )
                excluded_ids = {
                    photo_id
                    for photo_id, visibility_values in cover_roles.items()
                    if not any(visibility_values)
                }
                photos = [photo for photo in photos if photo["id"] not in excluded_ids]
            self.send_json({
                "ok": True,
                "group": {
                    "id": group["id"],
                    "name": group["name"],
                    "photos": photos,
                    "all_photos": list(group.get("photos", [])),
                    "cover_photo": cover_photo,
                    "story_cover_photo": story_cover_photo,
                } if group else None,
            })
            return
        if path.startswith("/api/groups/"):
            group_id = path.rsplit("/", 1)[-1]
            store, _ = ensure_store_schema(load_store())
            group = find_group(store, group_id)
            if not group:
                self.send_error_json("分组不存在", HTTPStatus.NOT_FOUND)
            else:
                self.send_json({"ok": True, "group": group})
            return
        super().do_GET()

    def do_HEAD(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        admin_path = get_admin_path()
        if path != admin_path and path.rstrip("/") == admin_path:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", admin_path)
            self.end_headers()
            return
        if path == admin_path:
            self.path = "/admin.html"
            super().do_HEAD()
            return
        if path == "/admin.html":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", admin_path)
            self.end_headers()
            return
        if path.startswith(("/uploads/", "/photos/")):
            self.serve_data_file(path)
            return
        if path == "/api/health":
            self.send_json({"ok": True, "version": APP_VERSION, "time": now_iso()})
            return
        super().do_HEAD()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/groups":
                self.create_group()
            elif path == "/api/groups/select":
                self.select_group()
            elif path == "/api/upload/photos":
                self.upload_photos()
            elif path == "/api/upload/zip":
                self.upload_zip()
            elif path == "/api/groups/cover":
                self.set_group_cover()
            elif path == "/api/groups/story-cover":
                self.set_group_story_cover()
            elif path == "/api/groups/detail-settings":
                self.set_group_detail_settings()
            elif path == "/api/groups/rename":
                self.rename_group()
            elif path == "/api/groups/delete":
                self.delete_group()
            elif path == "/api/photos/delete":
                self.delete_photo()
            elif path == "/api/settings/admin-path":
                self.update_admin_path()
            else:
                self.send_error_json("接口不存在", HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_error_json(str(error))
        except Exception as error:
            self.log_error("API error: %s", error)
            self.send_error_json("服务器处理失败", HTTPStatus.INTERNAL_SERVER_ERROR)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("请求内容过大")
        return json.loads(self.rfile.read(length) or b"{}")

    def read_form(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("上传内容超过 2GB 限制")
        return cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(length),
            },
            keep_blank_values=True,
        )

    def create_group(self):
        payload = self.read_json_body()
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("请输入分组名称")
        group = {
            "id": slug_id(),
            "name": name[:80],
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "photos": [],
            "cover_photo_id": None,
            "story_cover_photo_id": None,
            "show_cover_in_details": False,
            "show_story_cover_in_details": False,
        }
        with DATA_LOCK:
            store = load_store()
            store["groups"].append(group)
            active_group_id = UNCHANGED
            if not store.get("active_group_id"):
                store["active_group_id"] = group["id"]
                active_group_id = group["id"]
            save_group(
                group,
                len(store["groups"]) - 1,
                active_group_id=active_group_id,
            )
        self.send_json({"ok": True, "group": group}, HTTPStatus.CREATED)

    def select_group(self):
        payload = self.read_json_body()
        group_id = payload.get("group_id")
        with DATA_LOCK:
            store = load_store()
            if not find_group(store, group_id):
                raise ValueError("分组不存在")
            store["active_group_id"] = group_id
            set_active_group_id(group_id)
        self.send_json({"ok": True, "active_group_id": group_id})

    def rename_group(self):
        payload = self.read_json_body()
        group_id = payload.get("group_id")
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("请输入分组名称")
        with DATA_LOCK:
            store, _ = ensure_store_schema(load_store())
            group = find_group(store, group_id)
            if not group:
                raise ValueError("分组不存在")
            group["name"] = name[:80]
            group["updated_at"] = now_iso()
            save_group(group, store["groups"].index(group))
        self.send_json({"ok": True, "group": group})

    def delete_group(self):
        payload = self.read_json_body()
        group_id = payload.get("group_id")
        with DATA_LOCK:
            store, _ = ensure_store_schema(load_store())
            group = find_group(store, group_id)
            if not group:
                raise ValueError("分组不存在")
            store["groups"] = [
                item for item in store["groups"] if item["id"] != group_id
            ]
            if store.get("active_group_id") == group_id:
                store["active_group_id"] = (
                    store["groups"][0]["id"] if store["groups"] else None
                )
                active_group_id = store["active_group_id"]
            else:
                active_group_id = UNCHANGED
            delete_group_record(group_id, active_group_id)
        for photo in group.get("photos", []):
            remove_managed_file(store, photo)
        shutil.rmtree(UPLOADS_DIR / group_id, ignore_errors=True)
        self.send_json({
            "ok": True,
            "active_group_id": store.get("active_group_id"),
        })

    def delete_photo(self):
        payload = self.read_json_body()
        group_id = payload.get("group_id")
        photo_id = payload.get("photo_id")
        with DATA_LOCK:
            store, _ = ensure_store_schema(load_store())
            group = find_group(store, group_id)
            if not group:
                raise ValueError("分组不存在")
            photo = next(
                (item for item in group.get("photos", []) if item["id"] == photo_id),
                None,
            )
            if not photo:
                raise ValueError("照片不存在")
            group["photos"] = [
                item for item in group["photos"] if item["id"] != photo_id
            ]
            remaining = group["photos"]
            if group.get("cover_photo_id") == photo_id:
                group["cover_photo_id"] = remaining[0]["id"] if remaining else None
            if group.get("story_cover_photo_id") == photo_id:
                group["story_cover_photo_id"] = (
                    remaining[1]["id"]
                    if len(remaining) > 1
                    else (remaining[0]["id"] if remaining else None)
                )
            group["updated_at"] = now_iso()
            save_group(group, store["groups"].index(group), replace_photos=True)
        remove_managed_file(store, photo)
        self.send_json({
            "ok": True,
            "cover_photo_id": group.get("cover_photo_id"),
            "story_cover_photo_id": group.get("story_cover_photo_id"),
        })

    def set_group_cover(self):
        payload = self.read_json_body()
        group_id = payload.get("group_id")
        photo_id = payload.get("photo_id")
        with DATA_LOCK:
            store, _ = ensure_store_schema(load_store())
            group = find_group(store, group_id)
            if not group:
                raise ValueError("分组不存在")
            if not any(photo["id"] == photo_id for photo in group.get("photos", [])):
                raise ValueError("照片不存在")
            group["cover_photo_id"] = photo_id
            group["updated_at"] = now_iso()
            save_group(group, store["groups"].index(group))
        self.send_json({"ok": True, "cover_photo_id": photo_id})

    def set_group_story_cover(self):
        payload = self.read_json_body()
        group_id = payload.get("group_id")
        photo_id = payload.get("photo_id")
        with DATA_LOCK:
            store, _ = ensure_store_schema(load_store())
            group = find_group(store, group_id)
            if not group:
                raise ValueError("分组不存在")
            if not any(photo["id"] == photo_id for photo in group.get("photos", [])):
                raise ValueError("照片不存在")
            group["story_cover_photo_id"] = photo_id
            group["updated_at"] = now_iso()
            save_group(group, store["groups"].index(group))
        self.send_json({"ok": True, "story_cover_photo_id": photo_id})

    def set_group_detail_settings(self):
        payload = self.read_json_body()
        group_id = payload.get("group_id")
        with DATA_LOCK:
            store, _ = ensure_store_schema(load_store())
            group = find_group(store, group_id)
            if not group:
                raise ValueError("分组不存在")
            group["show_cover_in_details"] = bool(payload.get("show_cover_in_details"))
            group["show_story_cover_in_details"] = bool(
                payload.get("show_story_cover_in_details")
            )
            group["updated_at"] = now_iso()
            save_group(group, store["groups"].index(group))
        self.send_json({
            "ok": True,
            "show_cover_in_details": group["show_cover_in_details"],
            "show_story_cover_in_details": group["show_story_cover_in_details"],
        })

    def update_admin_path(self):
        payload = self.read_json_body()
        admin_path = normalize_admin_path(payload.get("admin_path"))
        with DATA_LOCK:
            set_setting("admin_path", admin_path)
        self.send_json({"ok": True, "admin_path": admin_path})

    def upload_photos(self):
        form = self.read_form()
        group_id = form.getfirst("group_id", "").strip()
        group_name = form.getfirst("group_name", "").strip()
        resolve_locations = form.getfirst("resolve_locations", "false") == "true"
        file_items = form["photos"] if "photos" in form else []
        if not isinstance(file_items, list):
            file_items = [file_items]
        file_items = [item for item in file_items if getattr(item, "filename", None)]
        if not file_items:
            raise ValueError("请选择至少一张照片")

        with DATA_LOCK:
            store = load_store()
            group = find_group(store, group_id) if group_id else None
            if not group:
                if not group_name:
                    raise ValueError("新建分组时必须填写名称")
                group = {
                    "id": slug_id(),
                    "name": group_name[:80],
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                    "photos": [],
                    "cover_photo_id": None,
                    "story_cover_photo_id": None,
                    "show_cover_in_details": False,
                    "show_story_cover_in_details": False,
                }
                store["groups"].append(group)

            uploaded = [
                save_uploaded_image(item, group["id"], resolve_locations)
                for item in file_items
            ]
            group["photos"].extend(uploaded)
            if not group.get("cover_photo_id") and uploaded:
                group["cover_photo_id"] = uploaded[0]["id"]
            if not group.get("story_cover_photo_id") and uploaded:
                group["story_cover_photo_id"] = (
                    uploaded[1]["id"] if len(uploaded) > 1 else uploaded[0]["id"]
                )
            group["updated_at"] = now_iso()
            active_group_id = UNCHANGED
            if not store.get("active_group_id"):
                store["active_group_id"] = group["id"]
                active_group_id = group["id"]
            save_group(
                group,
                store["groups"].index(group),
                replace_photos=True,
                active_group_id=active_group_id,
            )
        self.send_json({"ok": True, "group_id": group["id"], "uploaded": len(uploaded)})

    def upload_zip(self):
        form = self.read_form()
        group_name = form.getfirst("group_name", "").strip()
        resolve_locations = form.getfirst("resolve_locations", "false") == "true"
        archive_item = form["archive"] if "archive" in form else None
        if not group_name:
            raise ValueError("请输入新分组名称")
        if archive_item is None or not getattr(archive_item, "filename", None):
            raise ValueError("请选择 ZIP 压缩包")
        if Path(archive_item.filename).suffix.lower() != ".zip":
            raise ValueError("仅支持 ZIP 压缩包")

        group_id = slug_id()
        target_dir = UPLOADS_DIR / group_id
        target_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temporary:
            shutil.copyfileobj(archive_item.file, temporary)
            temp_path = Path(temporary.name)

        try:
            files = safe_extract_zip(temp_path, target_dir)
            if not files:
                shutil.rmtree(target_dir, ignore_errors=True)
                raise ValueError("压缩包中没有支持的图片")
            photos = [
                extract_photo_data(
                    path,
                    f"uploads/{group_id}/{path.name}",
                    original_name,
                    resolve_locations,
                )
                for path, original_name in files
            ]
        finally:
            temp_path.unlink(missing_ok=True)

        group = {
            "id": group_id,
            "name": group_name[:80],
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "photos": photos,
            "cover_photo_id": photos[0]["id"] if photos else None,
            "story_cover_photo_id": (
                photos[1]["id"] if len(photos) > 1 else (photos[0]["id"] if photos else None)
            ),
            "show_cover_in_details": False,
            "show_story_cover_in_details": False,
        }
        with DATA_LOCK:
            store = load_store()
            store["groups"].append(group)
            active_group_id = UNCHANGED
            if not store.get("active_group_id"):
                store["active_group_id"] = group_id
                active_group_id = group_id
            save_group(
                group,
                len(store["groups"]) - 1,
                replace_photos=True,
                active_group_id=active_group_id,
            )
        self.send_json({"ok": True, "group_id": group_id, "uploaded": len(photos)})


def main():
    initialize_store()
    port = int(os.environ.get("PHOTO_WALL_PORT", "8765"))
    host = os.environ.get("PHOTO_WALL_HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), PhotoWallHandler)
    print(f"Photo Wall: http://{host}:{port}")
    print(f"Admin:      http://{host}:{port}{get_admin_path()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
