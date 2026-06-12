# Photo Wall

一个受 Rockstar Games《Grand Theft Auto VI》官网视觉节奏启发的本地照片管理与叙事应用。

## 网页管理

启动服务后访问：

- 照片墙：`http://localhost:8765`
- 管理页面：`http://localhost:8765/admin`

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
- `admin.html` / `admin.css` / `admin.js`：管理端页面
- `server.py`：HTTP 服务、API、SQLite 持久化、上传处理
- `extract_exif.py`：读取上传图片的 EXIF 和可选 GPS 反查
- `Dockerfile` / `docker-compose.yml`：容器化部署配置
- `.github/workflows/docker-image.yml`：构建并推送 GHCR 镜像

## 使用方式

本地直接运行：

```bash
pip install -r requirements.txt
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
- `data/photos.json`：旧版照片数据导入来源
- `data/groups.json`：旧版分组数据导入来源
- `data/location_cache.json`：GPS 地点缓存

如需迁移旧数据，可把现有 `groups.json`、`photos.json`、`photos/`、`uploads/` 复制到 `data/` 后再首次启动容器。

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
