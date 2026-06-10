#!/usr/bin/env python3
import cgi
import json
import mimetypes
import os
import shutil
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
from urllib.parse import parse_qs, urlparse

import extract_exif


ROOT = Path(__file__).resolve().parent
GROUPS_FILE = ROOT / "groups.json"
UPLOADS_DIR = ROOT / "uploads"
LEGACY_PHOTOS_DIR = ROOT / "photos"
LEGACY_DATA_FILE = ROOT / "photos.json"
IMAGE_EXTS = extract_exif.IMAGE_EXTS
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_FILES = 2000
DATA_LOCK = threading.Lock()


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


def load_store():
    return read_json(GROUPS_FILE, {"active_group_id": None, "groups": []})


def save_store(store):
    write_json_atomic(GROUPS_FILE, store)


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
    if changed:
        save_store(store)
    return store


def initialize_store():
    UPLOADS_DIR.mkdir(exist_ok=True)
    if GROUPS_FILE.exists():
        return

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
    ensure_store_schema(store)


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
    target = (ROOT / relative_path).resolve()
    managed_roots = (UPLOADS_DIR.resolve(), LEGACY_PHOTOS_DIR.resolve())
    if any(target == root or root in target.parents for root in managed_roots):
        target.unlink(missing_ok=True)


def public_store(store):
    store = ensure_store_schema(store)
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
    server_version = "PhotoWall/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message, status=HTTPStatus.BAD_REQUEST):
        self.send_json({"ok": False, "error": message}, status)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if path == "/api/groups":
            with DATA_LOCK:
                self.send_json({"ok": True, **public_store(load_store())})
            return
        if path == "/api/active-photos":
            with DATA_LOCK:
                store = ensure_store_schema(load_store())
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
            with DATA_LOCK:
                group = find_group(ensure_store_schema(load_store()), group_id)
                if not group:
                    self.send_error_json("分组不存在", HTTPStatus.NOT_FOUND)
                else:
                    self.send_json({"ok": True, "group": group})
            return
        super().do_GET()

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
            else:
                self.send_error_json("接口不存在", HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_error_json(str(error))
        except Exception as error:
            self.log_error("API error: %s", error)
            self.send_error_json(f"服务器处理失败: {error}", HTTPStatus.INTERNAL_SERVER_ERROR)

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
            if not store.get("active_group_id"):
                store["active_group_id"] = group["id"]
            save_store(store)
        self.send_json({"ok": True, "group": group}, HTTPStatus.CREATED)

    def select_group(self):
        payload = self.read_json_body()
        group_id = payload.get("group_id")
        with DATA_LOCK:
            store = load_store()
            if not find_group(store, group_id):
                raise ValueError("分组不存在")
            store["active_group_id"] = group_id
            save_store(store)
        self.send_json({"ok": True, "active_group_id": group_id})

    def rename_group(self):
        payload = self.read_json_body()
        group_id = payload.get("group_id")
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("请输入分组名称")
        with DATA_LOCK:
            store = ensure_store_schema(load_store())
            group = find_group(store, group_id)
            if not group:
                raise ValueError("分组不存在")
            group["name"] = name[:80]
            group["updated_at"] = now_iso()
            save_store(store)
        self.send_json({"ok": True, "group": group})

    def delete_group(self):
        payload = self.read_json_body()
        group_id = payload.get("group_id")
        with DATA_LOCK:
            store = ensure_store_schema(load_store())
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
            save_store(store)
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
            store = ensure_store_schema(load_store())
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
            save_store(store)
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
            store = ensure_store_schema(load_store())
            group = find_group(store, group_id)
            if not group:
                raise ValueError("分组不存在")
            if not any(photo["id"] == photo_id for photo in group.get("photos", [])):
                raise ValueError("照片不存在")
            group["cover_photo_id"] = photo_id
            group["updated_at"] = now_iso()
            save_store(store)
        self.send_json({"ok": True, "cover_photo_id": photo_id})

    def set_group_story_cover(self):
        payload = self.read_json_body()
        group_id = payload.get("group_id")
        photo_id = payload.get("photo_id")
        with DATA_LOCK:
            store = ensure_store_schema(load_store())
            group = find_group(store, group_id)
            if not group:
                raise ValueError("分组不存在")
            if not any(photo["id"] == photo_id for photo in group.get("photos", [])):
                raise ValueError("照片不存在")
            group["story_cover_photo_id"] = photo_id
            group["updated_at"] = now_iso()
            save_store(store)
        self.send_json({"ok": True, "story_cover_photo_id": photo_id})

    def set_group_detail_settings(self):
        payload = self.read_json_body()
        group_id = payload.get("group_id")
        with DATA_LOCK:
            store = ensure_store_schema(load_store())
            group = find_group(store, group_id)
            if not group:
                raise ValueError("分组不存在")
            group["show_cover_in_details"] = bool(payload.get("show_cover_in_details"))
            group["show_story_cover_in_details"] = bool(
                payload.get("show_story_cover_in_details")
            )
            group["updated_at"] = now_iso()
            save_store(store)
        self.send_json({
            "ok": True,
            "show_cover_in_details": group["show_cover_in_details"],
            "show_story_cover_in_details": group["show_story_cover_in_details"],
        })

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
            if not store.get("active_group_id"):
                store["active_group_id"] = group["id"]
            save_store(store)
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
            if not store.get("active_group_id"):
                store["active_group_id"] = group_id
            save_store(store)
        self.send_json({"ok": True, "group_id": group_id, "uploaded": len(photos)})


def main():
    initialize_store()
    port = int(os.environ.get("PHOTO_WALL_PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), PhotoWallHandler)
    print(f"Photo Wall: http://localhost:{port}")
    print(f"Admin:      http://localhost:{port}/admin.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
