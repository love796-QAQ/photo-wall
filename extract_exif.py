#!/usr/bin/env python3
"""
EXIF Photo Scanner — 提取照片元数据并生成 photos.js
用法: python extract_exif.py
"""

import os
import sys
import json
import argparse
import time
import urllib.parse
import urllib.request

try:
    from PIL import Image
    from PIL.ExifTags import TAGS, GPSTAGS
except ImportError:
    print("请先安装 Pillow: pip install Pillow")
    sys.exit(1)

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.environ.get("PHOTO_WALL_DATA_DIR", ROOT))
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
OUTPUT_FILE = os.path.join(DATA_DIR, "photos.js")
OUTPUT_JSON = os.path.join(DATA_DIR, "photos.json")
LOCATION_CACHE = os.path.join(DATA_DIR, "location_cache.json")
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif'}
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"


def configure_paths(data_dir):
    global DATA_DIR, PHOTOS_DIR, OUTPUT_FILE, OUTPUT_JSON, LOCATION_CACHE
    DATA_DIR = os.path.abspath(data_dir)
    PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
    OUTPUT_FILE = os.path.join(DATA_DIR, "photos.js")
    OUTPUT_JSON = os.path.join(DATA_DIR, "photos.json")
    LOCATION_CACHE = os.path.join(DATA_DIR, "location_cache.json")


def dms_to_decimal(v):
    if not v or len(v) < 3:
        return None
    try:
        return float(v[0]) + float(v[1]) / 60.0 + float(v[2]) / 3600.0
    except (TypeError, ValueError):
        return None


def get_gps(info):
    gps = {}
    if not info:
        return None, None
    for tag_id, value in info.items():
        tag = TAGS.get(tag_id, tag_id)
        if tag == "GPSInfo" and value:
            for gps_tag_id, gps_value in value.items():
                gps_tag = GPSTAGS.get(gps_tag_id, gps_tag_id)
                gps[gps_tag] = gps_value
            break
    if not gps.get('GPSLatitude') or not gps.get('GPSLongitude'):
        return None, None
    lat = dms_to_decimal(gps['GPSLatitude'])
    lon = dms_to_decimal(gps['GPSLongitude'])
    if lat is None or lon is None:
        return None, None
    if gps.get('GPSLatitudeRef') == 'S':
        lat = -lat
    if gps.get('GPSLongitudeRef') == 'W':
        lon = -lon
    return round(lat, 6), round(lon, 6)


def extract(filepath):
    data = {}
    try:
        img = Image.open(filepath)
        info = img._getexif()
        if not info:
            return data
        for tag_id, value in info.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag in ('DateTimeOriginal', 'DateTime', 'DateTimeDigitized'):
                data[tag] = str(value)
            elif tag == 'Make':
                data['camera_make'] = str(value).strip()
            elif tag == 'Model':
                data['camera_model'] = str(value).strip()
        lat, lon = get_gps(info)
        if lat is not None:
            data['latitude'] = lat
            data['longitude'] = lon
    except Exception as e:
        print(f"  WARN {os.path.basename(filepath)}: {e}")
    return data


def format_date(raw):
    if not raw:
        return None
    try:
        parts = raw.replace(':', '-', 2).split(' ')
        d = parts[0].split('-')
        t = parts[1][:5] if len(parts) > 1 else ''
        month = str(int(d[1]))
        day = str(int(d[2]))
        if t:
            return f"{d[0]}年{month}月{day}日 {t}"
        return f"{d[0]}年{month}月{day}日"
    except Exception:
        return raw


def format_camera(make, model):
    if not make and not model:
        return None
    make = (make or '').strip()
    model = (model or '').strip()
    if model.startswith(make):
        return model
    return f"{make} {model}".strip()


def load_location_cache():
    if not os.path.isfile(LOCATION_CACHE):
        return {}
    try:
        with open(LOCATION_CACHE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"WARN 无法读取地点缓存: {e}")
        return {}


def location_key(lat, lon):
    return f"{lat:.6f},{lon:.6f}"


def save_location_cache(cache):
    with open(LOCATION_CACHE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)


def format_location(address):
    landmark = (
        address.get('amenity')
        or address.get('tourism')
        or address.get('historic')
        or address.get('building')
        or address.get('road')
        or address.get('quarter')
        or address.get('suburb')
    )
    district = address.get('city_district') or address.get('city') or address.get('county')
    iso_code = address.get('ISO3166-2-lvl4', '')
    municipality_names = {
        'CN-BJ': '北京市',
        'CN-SH': '上海市',
        'CN-TJ': '天津市',
        'CN-CQ': '重庆市',
    }
    municipality = municipality_names.get(iso_code) or address.get('municipality') or address.get('state')

    if municipality in ('北京市', '上海市', '天津市', '重庆市'):
        city_label = municipality[:-1] + (district.replace('区', '') if district else '')
    else:
        city_name = address.get('city') or address.get('town') or address.get('municipality')
        city_label = (city_name or '').replace('市', '')
        if district and district != city_name:
            city_label += district.replace('区', '')

    parts = []
    if landmark:
        parts.append(landmark)
    if city_label and city_label not in parts:
        parts.append(city_label)
    return ' · '.join(parts) or None


def reverse_geocode(lat, lon):
    query = urllib.parse.urlencode({
        'format': 'jsonv2',
        'lat': lat,
        'lon': lon,
        'accept-language': 'zh-CN',
        'zoom': 18,
        'addressdetails': 1,
    })
    request = urllib.request.Request(
        f"{NOMINATIM_URL}?{query}",
        headers={'User-Agent': 'PhotoWall/1.0 (local personal project)'},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.load(response)
    return format_location(payload.get('address', {}))


def parse_args():
    parser = argparse.ArgumentParser(description='提取照片 EXIF 并生成照片墙数据')
    parser.add_argument(
        '--resolve-locations',
        action='store_true',
        help='将未缓存的 GPS 坐标发送到 OpenStreetMap Nominatim 获取中文地点',
    )
    return parser.parse_args()


def write_outputs(photos):
    json_str = json.dumps(photos, ensure_ascii=False, indent=2)
    js_content = f"const photoData = {json_str};\n"
    js_temp = OUTPUT_FILE + '.tmp'
    json_temp = OUTPUT_JSON + '.tmp'

    with open(js_temp, 'w', encoding='utf-8') as f:
        f.write(js_content)
    with open(json_temp, 'w', encoding='utf-8') as f:
        json.dump(photos, f, ensure_ascii=False, indent=2)

    os.replace(js_temp, OUTPUT_FILE)
    os.replace(json_temp, OUTPUT_JSON)


def main():
    args = parse_args()
    if not os.path.isdir(PHOTOS_DIR):
        print(f"错误: 找不到 photos 目录: {PHOTOS_DIR}")
        print("请确保在项目根目录运行此脚本")
        sys.exit(1)

    files = sorted(
        f for f in os.listdir(PHOTOS_DIR)
        if os.path.splitext(f)[1].lower() in IMAGE_EXTS
    )
    if not files:
        print(f"在 {PHOTOS_DIR} 中没有找到图片文件")
        write_outputs([])
        print("已生成空照片列表")
        return

    print(f"找到 {len(files)} 张图片\n")
    photos = []
    location_cache = load_location_cache()
    cache_changed = False

    for i, filename in enumerate(files):
        filepath = os.path.join(PHOTOS_DIR, filename)
        rel_path = f"photos/{filename}"
        print(f"[{i+1}/{len(files)}] {filename}")

        photo = {"name": filename, "path": rel_path}
        exif = extract(filepath)

        date_raw = exif.get('DateTimeOriginal') or exif.get('DateTime') or exif.get('DateTimeDigitized')
        if date_raw:
            formatted = format_date(date_raw)
            if formatted:
                photo['date'] = formatted
                photo['date_raw'] = date_raw

        lat = exif.get('latitude')
        lon = exif.get('longitude')
        if lat is not None and lon is not None:
            photo['latitude'] = lat
            photo['longitude'] = lon
            location = location_cache.get(location_key(lat, lon))
            if not location and args.resolve_locations:
                try:
                    print("  Location: 正在查询中文地点...")
                    location = reverse_geocode(lat, lon)
                    if location:
                        location_cache[location_key(lat, lon)] = location
                        cache_changed = True
                    time.sleep(1.1)
                except Exception as e:
                    print(f"  WARN 地点查询失败: {e}")
            if location:
                photo['location'] = location
                print(f"  Location: {location}")
            else:
                print("  Location: 未在本地缓存中命名")

        camera = format_camera(exif.get('camera_make'), exif.get('camera_model'))
        if camera:
            photo['camera'] = camera
            print(f"  Camera: {camera}")
        if date_raw:
            print(f"  Date: {photo.get('date', date_raw)}")

        photos.append(photo)

    if cache_changed:
        save_location_cache(location_cache)

    write_outputs(photos)

    # Summary
    with_gps = sum(1 for p in photos if p.get('location'))
    with_date = sum(1 for p in photos if p.get('date'))
    with_camera = sum(1 for p in photos if p.get('camera'))
    print(f"\n{'='*50}")
    print(f"完成! {len(photos)} 张照片")
    print(f"   含GPS: {with_gps}     含日期: {with_date}     含相机: {with_camera}")
    print(f"   输出: {OUTPUT_FILE}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
