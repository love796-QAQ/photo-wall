# Photo Wall

一个受 Rockstar Games《Grand Theft Auto VI》官网视觉节奏启发的本地照片管理与叙事应用。

## 网页管理

启动 `server.py` 后访问：

- 照片墙：`http://localhost:8765`
- 管理页面：`http://localhost:8765/admin.html`

管理页面支持：

- 单张或多张照片上传
- 上传到已有分组
- 上传照片时新建分组
- ZIP 压缩包自动解压并创建新分组
- EXIF 日期、相机和 GPS 自动提取
- 可选的 GPS 中文地点联网解析
- 选择照片墙当前展示的分组
- 文件唯一命名，新增内容不会覆盖旧照片

## 当前设计

- 固定极简导航与全屏照片索引
- 全屏首图、滚动缩放标题、暗场与闪光转场
- 渐变大标题发布区
- Sticky 封面与不对称图文拼贴章节
- 图片滚动视差与进入动画
- 横向拖动的完整照片档案
- 支持键盘、触摸手势的 Lightbox
- 桌面端和移动端响应式布局
- `prefers-reduced-motion` 无障碍降级

页面只借鉴滚动叙事、构图和视觉节奏，不包含 Rockstar 的商标、字体或官方素材。

## 文件职责

- `index.html`：页面结构、导航、首屏、档案区和 Lightbox
- `style.css`：全部视觉样式、响应式布局和动效
- `script.js`：照片章节生成、滚动进度、视差、菜单和 Lightbox
- `photo_wall.db`：运行时 SQLite 数据库，保存分组、展示设置和照片元数据
- `groups.json`：旧版分组数据；首次启动时会自动导入 SQLite
- `photos.js`：旧版浏览器照片数据
- `photos.json`：旧版照片数据的 JSON 版本；无分组数据时用于初始化默认相册
- `extract_exif.py`：读取照片 EXIF 并生成两个数据文件
- `scan_photos.ps1`：执行一次照片扫描
- `auto_sync_photos.ps1`：监听照片目录并自动更新数据

## 使用方式

双击 `start_server.bat`，或在项目目录运行：

```powershell
python server.py
```

然后访问 `http://localhost:8765`。

## Docker 部署

构建并启动：

```bash
docker compose up -d --build
```

使用 GitHub Actions 推送到 GHCR 后，服务器可直接拉取镜像：

```bash
PHOTO_WALL_IMAGE=ghcr.io/<owner>/<repo>:latest docker compose up -d
```

容器默认监听 `8765`，Compose 只绑定到宿主机 `127.0.0.1:8765`，适合放在 Nginx 后面反向代理。

运行时数据挂载到项目目录的 `data/`：

- `data/photo_wall.db`：SQLite 数据库
- `data/uploads/`：管理后台上传的图片
- `data/photos/`：兼容旧版本地照片目录
- `data/location_cache.json`：GPS 地点缓存

如需迁移旧数据，可把现有 `groups.json`、`photos.json`、`photos/`、`uploads/` 复制到 `data/` 后再首次启动容器。

添加或删除照片后运行：

```powershell
.\scan_photos.ps1
```

持续监听照片目录：

```powershell
.\auto_sync_photos.ps1
```

也可以双击 `start_auto_sync.bat`。此模式只读取本地 EXIF，并使用已有地点缓存。

若希望新照片中的陌生 GPS 坐标也自动转换成中文地点，可双击：

```text
start_auto_sync_with_locations.bat
```

该模式会将尚未缓存的 GPS 坐标发送到 OpenStreetMap Nominatim，并把返回结果写入 `location_cache.json`。之后同一坐标无需再次联网查询。

Python 扫描依赖 Pillow：

```powershell
pip install Pillow
```

## 数据格式

```js
{
  name: "IMG_4376.JPEG",
  path: "photos/IMG_4376.JPEG",
  date: "2024年3月15日",
  camera: "Apple iPhone 14 Pro",
  location: "上海, 黄浦区"
}
```

`name` 和 `path` 为必填字段，其他字段缺失时页面会自动使用文件名和默认文案。

## 性能提示

当前照片约 31 MB。页面只预载开场所需的前五张照片，其余图片使用浏览器懒加载。若用于公网发布，建议额外生成 WebP/AVIF 与缩略图。
