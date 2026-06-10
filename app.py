# ============================================
# m3u8 搜索工具 - Python 后端
# 功能：接收前端搜索请求 -> 爬取目标站点 -> 解析 m3u8 地址 -> 返回 JSON
# 更新：前端分页，后端按页爬取
# 运行方式：python app.py
# 访问地址：http://192.168.70.172:5000（或 http://127.0.0.1:5000）
# ============================================

# === 导入模块 ===
import os
import sys
# 强制 Python 输出使用 UTF-8（解决 Windows GBK 终端中文崩溃）
sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
from flask import Flask, request, jsonify, render_template
import requests
from bs4 import BeautifulSoup
import re
import urllib.parse
import urllib3
# 禁用 SSL 警告（因为用了 verify=False）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import threading
import subprocess
import uuid
import json
import time
import shutil
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================
# Flask 初始化
# ============================================
app = Flask(__name__)

# ============================================
# 配置项
# ============================================
TARGET_BASE_URL = "http://192.168.70.188"
TARGET_SEARCH_URL = TARGET_BASE_URL + "/index.php/vod/search.html"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": TARGET_BASE_URL + "/",
}


# ============================================
# 工具函数：提取 m3u8 地址
# ============================================
def extract_m3u8_urls(dd_text):
    """从文本中提取所有 .m3u8 链接"""
    if not dd_text:
        return []
    return re.findall(r'(https?://[^\s$]+?\.m3u8)', dd_text)


# ============================================
# 核心函数：解析单页搜索结果
# ============================================
def parse_search_page(html):
    """
    解析搜索页 HTML，返回视频列表和分页信息
    返回: (videos, page_info)
    """
    videos = []
    page_info = None

    soup = BeautifulSoup(html, 'lxml')

    # ---- 分页信息 ----
    tip = soup.select_one('.page_tip')
    if tip:
        text = tip.get_text(strip=True)
        # 兼容有逗号和没逗号的格式：共65条数据,当前1/7页
        m = re.search(r'共(\d+)条数据,?\s*当前(\d+)/(\d+)页', text)
        if m:
            page_info = {
                'total_items': int(m.group(1)),
                'current': int(m.group(2)),
                'total': int(m.group(3))
            }

    # ---- 视频列表 ----
    show_list = soup.find('ul', class_='show-list')
    if not show_list:
        return videos, page_info

    for li in show_list.find_all('li', recursive=False):
        video = {}

        # 标题
        h2 = li.find('h2')
        if h2:
            a = h2.find('a')
            video['title'] = (a.get_text(strip=True) if a else h2.get_text(strip=True))
        else:
            video['title'] = '未知标题'

        # 详情链接 + 封面
        img_link = li.find('a', class_='play-img')
        if img_link and img_link.get('href'):
            p = img_link['href']
            video['detail_url'] = TARGET_BASE_URL + p if p.startswith('/') else p
            img = img_link.find('img')
            img_src = img['src'] if img and img.get('src') else ''
            # 相对路径补全为完整 URL
            video['cover_img'] = (TARGET_BASE_URL + img_src) if img_src.startswith('/') else img_src
        else:
            video['detail_url'] = ''
            video['cover_img'] = ''

        # m3u8 地址
        m3u8_list = []
        for dl in li.find_all('dl'):
            dt = dl.find('dt')
            if dt and '地址' in dt.get_text():
                dd = dl.find('dd')
                if dd:
                    m3u8_list = extract_m3u8_urls(dd.get_text())
                break
        video['m3u8_urls'] = m3u8_list

        videos.append(video)

    return videos, page_info


# ============================================
# 核心函数：构建分页URL模板
# ============================================
def get_page_pattern(html):
    """从第1页HTML中提取分页URL模板"""
    soup = BeautifulSoup(html, 'lxml')
    link = soup.select_one('a.page_link[href*="/page/2/"]')
    if link and link.get('href'):
        href = link['href']
        pattern = re.sub(r'/page/\d+/', '/page/{page}/', href)
        return TARGET_BASE_URL + pattern
    return None


# ============================================
# 核心函数：按页爬取
# ============================================
def fetch_page(keyword, page_num):
    """
    爬取指定页码的搜索结果
    如果是第1页，返回分页信息和分页URL模板
    """
    encoded = urllib.parse.quote(keyword, encoding='utf-8')

    if page_num == 1:
        url = f"{TARGET_SEARCH_URL}?wd={encoded}"
    else:
        # 先获取第1页，解析分页模板
        try:
            r1 = requests.get(f"{TARGET_SEARCH_URL}?wd={encoded}", headers=HEADERS, timeout=15)
            r1.encoding = 'utf-8'
            if r1.status_code != 200:
                return None, None, None, None
            html1 = r1.text
            # 获取总页数和第一个页的数据
            # 重新从第1页HTML中获取page_info
            _, page_info = parse_search_page(html1)
            pattern = get_page_pattern(html1)
            if not pattern or not page_info or page_info.get('total', 1) < page_num:
                return None, page_info, None, None
            url = pattern.replace('{page}', str(page_num))
        except Exception as e:
            print(f"[ERROR] get page pattern failed: {e}")
            return None, None, None, None

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        if resp.status_code != 200:
            return None, None, None, None
        videos, page_info = parse_search_page(resp.text)
        return videos, page_info, url, (resp.text if page_num == 1 else None)
    except Exception as e:
        print(f"[ERROR] request failed: {e}")
        return None, None, None, None


# ============================================
# 路由1：首页
# ============================================
@app.route('/')
def index():
    return render_template('index.html', target_url=TARGET_BASE_URL)


# ============================================
# 路由2：搜索 API（按页）
# ============================================
@app.route('/api/search')
def api_search():
    """
    搜索接口
    参数:
        wd  - 搜索关键词（必填）
        page - 页码（可选，默认1）
    """
    keyword = request.args.get('wd', '').strip()
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1

    if page < 1:
        page = 1

    if not keyword:
        return jsonify({"code": 400, "message": "请输入搜索关键词", "data": [], "total": 0, "page_info": None})

    # 安全打印（防止中文导致 Windows 终端 GBK 崩溃）
    safe_kw = keyword.encode('ascii', 'backslashreplace').decode('ascii')
    print(f"\n======= search: {safe_kw} page={page} =======")

    videos, page_info, _, _ = fetch_page(keyword, page)
    if videos is None:
        videos = []

    return jsonify({
        "code": 200,
        "message": f"找到 {len(videos)} 个结果",
        "data": videos,
        "total": len(videos),
        "page_info": page_info
    })


# ============================================
# 路由3：服务器检测
# ============================================
@app.route('/api/check-server')
def api_check_server():
    try:
        resp = requests.get(TARGET_BASE_URL, headers=HEADERS, timeout=5)
        return jsonify({"online": resp.status_code == 200, "status_code": resp.status_code, "server": TARGET_BASE_URL})
    except Exception as e:
        return jsonify({"online": False, "error": str(e), "server": TARGET_BASE_URL})


# ============================================
# 下载引擎 - 多任务管理
# ============================================

# 下载任务存储
# download_tasks[task_id] = {
#     'id': str, 'url': str, 'title': str,
#     'save_dir': str, 'episode': int,
#     'status': 'queued'|'downloading'|'completed'|'failed'|'cancelled',
#     'progress': 0-100, 'output_file': str|None,
#     'error': str|None, 'created_at': str, 'started_at': str|None
# }
# 持久化存储文件路径
TASKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'download_tasks.json')

download_tasks = {}
download_lock = threading.RLock()


def save_tasks():
    """保存任务到 JSON 文件（调用者保证线程安全）"""
    try:
        with open(TASKS_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(download_tasks.values()), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[save_tasks] error: {e}")


def load_tasks():
    """从 JSON 文件加载任务"""
    global download_tasks
    try:
        if os.path.exists(TASKS_FILE):
            with open(TASKS_FILE, 'r', encoding='utf-8') as f:
                tasks_list = json.load(f)
            download_tasks = {t['id']: t for t in tasks_list}
            print(f"[load_tasks] loaded {len(download_tasks)} tasks")
    except Exception as e:
        print(f"[load_tasks] error: {e}")


# 启动时加载已有任务
load_tasks()


def find_ffmpeg():
    """在系统 PATH 和常见安装路径中查找 ffmpeg"""
    # 1. 先查 PATH
    import shutil
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg:
        return ffmpeg
    # 2. 常见 WinGet 安装路径
    win_get_paths = [
        os.path.join(os.environ.get('LOCALAPPDATA', ''),
                     'Microsoft', 'WinGet', 'Packages',
                     'Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe'),
        os.path.join(os.environ.get('PROGRAMFILES', ''), 'ffmpeg', 'bin'),
        os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'ffmpeg', 'bin'),
    ]
    for base in win_get_paths:
        if not os.path.isdir(base):
            continue
        # 递归查找 ffmpeg.exe
        for root, dirs, files in os.walk(base):
            for f in files:
                if f.lower() == 'ffmpeg.exe':
                    return os.path.join(root, f)
    return 'ffmpeg'  # fallback


FFMPEG_PATH = find_ffmpeg()
print(f"[FFmpeg] using: {FFMPEG_PATH}")


def get_default_save_dir():
    """获取默认下载目录"""
    return os.path.join(os.path.expanduser('~'), 'Downloads', 'm3u8_downloads')


def sanitize_filename(name):
    """清理文件名中的非法字符"""
    return re.sub(r'[\\/*?:"<>|]', '_', name)


def resolve_url(base_url, path):
    """将相对路径解析为绝对 URL"""
    if path.startswith('http'):
        return path
    base = base_url.rsplit('/', 1)[0] + '/'
    return base + path


def parse_m3u8_segments(url, retry=True):
    """
    解析 m3u8 播放列表（带 60 秒缓存），获取所有分段 URL 和总时长
    返回 (segment_urls_list, total_duration_seconds)
    """
    # 检查缓存
    now = time.time()
    cached = _m3u8_cache.get(url)
    if cached and now - cached[0] < 60:
        return cached[1], cached[2]

    try:
        with _m3u8_parse_lock:
            # 双重检查锁：拿到锁后可能已被其他线程更新
            cached = _m3u8_cache.get(url)
            if cached and now - cached[0] < 60:
                return cached[1], cached[2]

            resp = requests.get(url, headers=HEADERS, timeout=30, verify=False)
            resp.encoding = 'utf-8'
            content = resp.text

            if '#EXT-X-STREAM-INF' in content:
                for line in content.split('\n'):
                    line = line.strip()
                    if line and not line.startswith('#') and line.endswith('.m3u8'):
                        sub_url = resolve_url(url, line)
                        return parse_m3u8_segments(sub_url)

            segments = []
            total_duration = 0.0
            for line in content.split('\n'):
                line = line.strip()
                if line.startswith('#EXTINF:'):
                    m = re.search(r'#EXTINF:\s*([\d.]+)', line)
                    if m:
                        total_duration += float(m.group(1))
                elif line and not line.startswith('#'):
                    seg_url = resolve_url(url, line)
                    segments.append(seg_url)

            if len(segments) < 50 and retry:
                print(f"[parse_m3u8] only {len(segments)} segs, retrying...")
                time.sleep(1)
                return parse_m3u8_segments(url, retry=False)

            # 写入缓存
            _m3u8_cache[url] = (time.time(), list(segments), total_duration)
            return segments, total_duration
    except Exception as e:
        print(f"[parse_m3u8] error: {e}")
        if retry:
            time.sleep(1)
            return parse_m3u8_segments(url, retry=False)
        return [], 0


def update_task_progress(task_id, **kwargs):
    """线程安全地更新任务字段"""
    with download_lock:
        if task_id in download_tasks:
            for k, v in kwargs.items():
                download_tasks[task_id][k] = v


DEFAULT_THREAD_COUNT = 36
DEFAULT_MAX_CONCURRENT = 3

# m3u8 解析锁 + 结果缓存（60秒过期），避免并发请求被 CDN 限流
_m3u8_parse_lock = threading.RLock()
_m3u8_cache = {}  # url -> (timestamp, segments_list, duration)

task_queue = []
task_queue_lock = threading.Lock()


def count_active_tasks():
    """统计当前正在下载或排队中的任务数"""
    with download_lock:
        return sum(1 for t in download_tasks.values()
                   if t.get('status') in ('downloading', 'queued', 'merging'))


def try_start_queued():
    """检查并启动等待队列中的任务"""
    tid_to_start = None
    with download_lock:
        limit = DEFAULT_MAX_CONCURRENT
        active = sum(1 for t in download_tasks.values()
                     if t.get('status') in ('downloading', 'queued', 'merging'))
        if active >= limit:
            return
        for t in download_tasks.values():
            if t.get('status') == 'waiting':
                t['status'] = 'queued'
                tid_to_start = t['id']
                break
        if tid_to_start:
            t = download_tasks.get(tid_to_start)
            if t:
                _start_download_thread(tid_to_start, t)


def _start_download_thread(tid, t):
    """启动单个下载线程"""
    thread = threading.Thread(
        target=download_worker,
        args=(tid, t['url'], t['title'], t['save_dir'],
              t.get('episode', 0), t.get('thread_count', DEFAULT_THREAD_COUNT)),
        daemon=True
    )
    thread.start()


def download_worker(task_id, url, title, save_dir, episode_num=0, thread_count=None):
    """
    分段并行下载 + ffmpeg 合并
    1. 解析 m3u8 获取所有分段 URL
    2. 多线程（默认36）并行下载所有分段
    3. requests.Session 复用 TCP 连接
    4. 精确进度 = 已完成分段数 / 总分段数
    5. ffmpeg concat 合并为 mp4
    """
    temp_dir = None
    if thread_count is None:
        thread_count = DEFAULT_THREAD_COUNT

    try:
        os.makedirs(save_dir, exist_ok=True)

        safe_title = sanitize_filename(title)[:80]
        if episode_num > 0:
            filename = f"{safe_title}_E{episode_num:02d}.mp4"
        else:
            filename = f"{safe_title}.mp4"

        output_path = os.path.join(save_dir, filename)
        counter = 1
        while os.path.exists(output_path):
            name_no_ext = filename[:-4]
            output_path = os.path.join(save_dir, f"{name_no_ext}_{counter}.mp4")
            counter += 1

        update_task_progress(task_id, status='downloading', output_file=output_path,
                             started_at=datetime.now().isoformat(), speed='', progress=0,
                             total_bytes=0, file_size='')
        save_tasks()

        # === 解析 m3u8 获取所有分段 URL ===
        segments, total_dur = parse_m3u8_segments(url)
        total_segs = len(segments)

        print(f"[DOWNLOAD] [{task_id}] parsed {total_segs} segs, {total_dur:.1f}s")

        # 分段太少（<3）时回退到 ffmpeg 直接下载
        if total_segs < 3:
            print(f"[DOWNLOAD] [{task_id}] too few segments ({total_segs}), fallback to ffmpeg")
            update_task_progress(task_id, status='downloading', progress=0, speed='')
            save_tasks()
            _ffmpeg_direct(task_id, url, output_path, total_dur)
            return

        print(f"[DOWNLOAD] [{task_id}] {total_segs} segs, {total_dur:.1f}s, {thread_count} threads")

        # === 创建临时目录 ===
        temp_dir = os.path.join(save_dir, f'.tmp_{task_id}')
        os.makedirs(temp_dir, exist_ok=True)

        # === 并行下载（参考 m3u8-downloader 的切片粒度） ===
        seg_lock = threading.Lock()
        completed_segs = [0]
        total_bytes = [0]
        start_time = time.time()
        # 初始化切片状态: 0=待下载, 1=成功, 2=失败
        seg_status = [0] * total_segs
        update_task_progress(task_id, seg_status=seg_status)

        session = requests.Session()
        session.verify = False  # 跳过 SSL 验证
        session.headers.update(HEADERS)
        seg_ref = url.rsplit('/', 1)[0] + '/'
        session.headers['Referer'] = seg_ref

        def dl_one_seg(seg_url, idx):
            # 重试一次，第二次用新解析的 URL（应对 CDN sign 过期）
            for attempt in range(2):
                try:
                    u = seg_url if attempt == 0 else _fresh_seg_url(url, idx)
                    if not u:
                        break
                    resp = session.get(u, timeout=120)
                    if resp.status_code == 200:
                        out_path = os.path.join(temp_dir, f'seg_{idx:05d}.ts')
                        with open(out_path, 'wb') as f:
                            f.write(resp.content)
                        with seg_lock:
                            seg_status[idx] = 1
                            completed_segs[0] += 1
                            total_bytes[0] += len(resp.content)
                        return True
                except:
                    pass
            with seg_lock:
                seg_status[idx] = 2
            return False

        def _fresh_seg_url(main_url, idx):
            """重新解析 m3u8 获取指定索引的分段 URL（应对 sign 过期）"""
            try:
                fresh_segs, _ = parse_m3u8_segments(main_url)
                if fresh_segs and idx < len(fresh_segs):
                    return fresh_segs[idx]
            except:
                pass
            return None

        futures = {}
        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            for idx, seg_url in enumerate(segments):
                futures[executor.submit(dl_one_seg, seg_url, idx)] = idx
            for f in as_completed(futures):
                with download_lock:
                    if task_id in download_tasks and download_tasks[task_id].get('status') == 'cancelled':
                        executor.shutdown(wait=False, cancel_futures=True)
                        break
                with seg_lock:
                    done = completed_segs[0]
                    tb = total_bytes[0]
                pct = int(done * 100 / total_segs)
                elapsed = time.time() - start_time
                speed_bps = tb / elapsed if elapsed > 0.5 else 0
                update_task_progress(task_id, progress=pct,
                                     speed=format_speed(speed_bps) if speed_bps > 0 else '',
                                     total_bytes=tb, file_size=format_size(tb),
                                     seg_status=seg_status[:])

        # === 检查取消（锁内只读状态，锁外做慢操作） ===
        cancelled = False
        with download_lock:
            if task_id in download_tasks and download_tasks[task_id].get('status') == 'cancelled':
                cancelled = True
        if cancelled:
            shutil.rmtree(temp_dir, ignore_errors=True)
            with download_lock:
                save_tasks()
            return

        with seg_lock:
            ok_segs = sum(1 for s in seg_status if s == 1)
        fail_segs = sum(1 for s in seg_status if s == 2)

        if ok_segs == 0:
            update_task_progress(task_id, status='failed', error='所有分段都下载失败', speed='')
            shutil.rmtree(temp_dir, ignore_errors=True)
            with download_lock:
                save_tasks()
            return

        # 用已下载的分段合并（参考 m3u8-downloader：有成功的就合并）
        update_task_progress(task_id, progress=95, status='merging', speed='')

        concat_file = os.path.join(temp_dir, 'concat.txt')
        with open(concat_file, 'w', encoding='utf-8') as f:
            for i in range(total_segs):
                seg_path = os.path.join(temp_dir, f'seg_{i:05d}.ts')
                if os.path.exists(seg_path):
                    f.write(f"file '{seg_path.replace(chr(92), '/')}'\n")

        print(f"[DOWNLOAD] [{task_id}] merging {ok_segs}/{total_segs} segs -> {output_path}")
        merge_cmd = [FFMPEG_PATH, '-f', 'concat', '-safe', '0',
                     '-i', concat_file, '-c', 'copy', '-y', output_path]
        merge_proc = subprocess.run(merge_cmd, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=600)
        shutil.rmtree(temp_dir, ignore_errors=True)

        if merge_proc.returncode == 0:
            final_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
            err_msg = '' if fail_segs == 0 else f'({fail_segs}个分片失败已忽略)'
            update_task_progress(task_id, status='completed', progress=100, speed='',
                                 total_bytes=final_size, file_size=format_size(final_size),
                                 error=err_msg)
            print(f"[DOWNLOAD] [{task_id}] completed {err_msg}: {format_size(final_size)}")
        else:
            err = merge_proc.stderr[-200:] if merge_proc.stderr else 'merge failed'
            update_task_progress(task_id, status='failed', speed='',
                                 error=f'合并失败: {err[:100]}')
        with download_lock:
            save_tasks()
        try_start_queued()

    except Exception as e:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        update_task_progress(task_id, status='failed', error=str(e), speed='')
        with download_lock:
            save_tasks()
        try_start_queued()
        print(f"[DOWNLOAD] [{task_id}] exception: {e}")


def format_size(bytes_val):
    """格式化文件大小"""
    if bytes_val < 1024:
        return f'{bytes_val}B'
    elif bytes_val < 1024 * 1024:
        return f'{bytes_val / 1024:.0f}KB'
    elif bytes_val < 1024 * 1024 * 1024:
        return f'{bytes_val / 1024 / 1024:.1f}MB'
    else:
        return f'{bytes_val / 1024 / 1024 / 1024:.2f}GB'


def _ffmpeg_direct(task_id, url, output_path, total_dur=0):
    """ffmpeg 直接下载（分段<3时的回退方案）"""
    _ffmpeg_start = [0.0]
    total_dur_us = int(total_dur * 1_000_000)
    last_lines = []
    try:
        ff_ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0'
        ff_ref = url.rsplit('/', 1)[0] + '/'
        cmd = [FFMPEG_PATH,
               '-headers', f'Referer: {ff_ref}\r\nUser-Agent: {ff_ua}\r\n',
               '-tls_verify', '0',
               '-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '5',
               '-nostdin',
               '-i', url,
               '-c', 'copy', '-bsf:a', 'aac_adtstoasc', '-y',
               '-timeout', '60', '-progress', 'pipe:1', output_path]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=True, encoding='utf-8', errors='replace')
        _ffmpeg_start[0] = time.time()

        for line in process.stdout:
            last_lines.append(line.strip())
            if len(last_lines) > 20:
                last_lines.pop(0)
            if line.startswith('progress=') and 'end' in line.strip():
                update_task_progress(task_id, progress=100, speed='')
                break
            ls = line.strip()
            if ls.startswith('out_time_us='):
                try:
                    us = int(ls.split('=')[1])
                    if total_dur_us > 0:
                        pct = min(int(us * 100 / total_dur_us), 99)
                    else:
                        sec = us / 1_000_000
                        pct = min(int(sec * 90 / (sec + 60)), 90)
                    if pct > 0:
                        update_task_progress(task_id, progress=pct)
                except:
                    pass
            elif ls.startswith('total_size='):
                try:
                    tb = int(ls.split('=')[1])
                    elapsed = time.time() - _ffmpeg_start[0]
                    if elapsed > 1:
                        update_task_progress(task_id, speed=format_speed(tb / elapsed),
                                             file_size=format_size(tb))
                except:
                    pass

        process.wait()
        if process.returncode == 0:
            final_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
            update_task_progress(task_id, status='completed', progress=100, speed='',
                                 file_size=format_size(final_size))
        else:
            # 提取 ffmpeg 的实际错误信息
            err_detail = 'ffmpeg 出错'
            for ln in last_lines:
                if any(kw in ln.lower() for kw in ['error', 'invalid', '404', '403', '500', 'timeout', 'cannot']):
                    err_detail = ln[:150]
                    break
            update_task_progress(task_id, status='failed', error=err_detail, speed='')
        save_tasks()
    except Exception as e:
        update_task_progress(task_id, status='failed', error=f'ffmpeg: {str(e)[:100]}', speed='')
        save_tasks()


def format_speed(bps):
    """格式化下载速度"""
    if bps < 1024:
        return f"{bps:.0f} B/s"
    elif bps < 1024 * 1024:
        return f"{bps/1024:.0f} KB/s"
    else:
        return f"{bps/1024/1024:.1f} MB/s"


def get_save_dir_with_sub(save_dir, sub_dir):
    """拼接保存路径与子目录（搜索页输入的目录名）"""
    if sub_dir:
        safe_sub = sanitize_filename(sub_dir)[:50]
        return os.path.join(save_dir, safe_sub)
    return save_dir


# ============================================
# 下载 API 路由
# ============================================

@app.route('/api/download', methods=['POST'])
def api_download():
    """
    启动单个下载任务
    请求体 JSON：{ url, title, save_dir, episode, sub_dir }
    sub_dir: 搜索页输入的目录名，作为子目录
    """
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    title = (data.get('title') or '未知视频').strip()
    save_dir = (data.get('save_dir') or get_default_save_dir()).strip()
    episode = int(data.get('episode', 0))
    sub_dir = (data.get('sub_dir') or '').strip()
    thread_count = int(data.get('thread_count', DEFAULT_THREAD_COUNT))

    if not url:
        return jsonify({'code': 400, 'message': '缺少下载地址'})

    actual_save_dir = get_save_dir_with_sub(save_dir, sub_dir)

    # 检查并发限制
    active = count_active_tasks()
    init_status = 'queued' if active < DEFAULT_MAX_CONCURRENT else 'waiting'
    status_msg = f'下载已开始: {title}' if init_status == 'queued' else '已加入等待队列'

    task_id = str(uuid.uuid4())[:8]
    now_iso = datetime.now().isoformat()

    with download_lock:
        download_tasks[task_id] = {
            'id': task_id,
            'url': url,
            'title': title,
            'save_dir': actual_save_dir,
            'episode': episode,
            'sub_dir': sub_dir,
            'status': init_status,
            'progress': 0,
            'created_at': now_iso,
            'started_at': None,
            'output_file': None,
            'error': None,
            'speed': '',
            'total_bytes': 0,
            'file_size': '',
            'seg_status': [],
            'thread_count': thread_count
        }
        save_tasks()

    if init_status == 'queued':
        with download_lock:
            t = download_tasks[task_id]
        _start_download_thread(task_id, t)

    return jsonify({
        'code': 200,
        'task_id': task_id,
        'message': status_msg,
        'status': init_status
    })


@app.route('/api/download/batch', methods=['POST'])
def api_download_batch():
    """
    批量启动下载
    请求体 JSON：{ items: [{url, title, episode}], save_dir, sub_dir }
    """
    data = request.get_json(silent=True) or {}
    items = data.get('items') or []
    save_dir = (data.get('save_dir') or get_default_save_dir()).strip()
    start_episode = int(data.get('start_episode', 1))
    sub_dir = (data.get('sub_dir') or '').strip()

    if not items:
        return jsonify({'code': 400, 'message': '没有要下载的项目'})

    actual_save_dir = get_save_dir_with_sub(save_dir, sub_dir)

    task_ids = []
    now_iso = datetime.now().isoformat()

    thread_count = int(data.get('thread_count', DEFAULT_THREAD_COUNT))

    started_count = 0
    queued_count = 0

    with download_lock:
        for idx, item in enumerate(items):
            url = (item.get('url') or '').strip()
            title = (item.get('title') or '未知视频').strip()
            ep = start_episode + idx if start_episode > 0 else 0

            if not url:
                continue

            active = count_active_tasks()
            init_status = 'queued' if active + queued_count < DEFAULT_MAX_CONCURRENT else 'waiting'

            tid = str(uuid.uuid4())[:8]
            download_tasks[tid] = {
                'id': tid,
                'url': url,
                'title': title,
                'save_dir': actual_save_dir,
                'episode': ep,
                'sub_dir': sub_dir,
                'status': init_status,
                'progress': 0,
                'created_at': now_iso,
                'started_at': None,
                'output_file': None,
                'error': None,
                'speed': '',
                'total_bytes': 0,
                'file_size': '',
                'seg_status': [],
                'thread_count': thread_count
            }
            task_ids.append(tid)
            if init_status == 'queued':
                started_count += 1
            else:
                queued_count += 1
        save_tasks()

    for tid in task_ids[:started_count]:
        with download_lock:
            t = download_tasks[tid]
        _start_download_thread(tid, t)

    return jsonify({
        'code': 200,
        'task_ids': task_ids,
        'count': len(task_ids),
        'message': f'已提交 {len(task_ids)} 个下载任务'
    })


@app.route('/api/download/list')
def api_download_list():
    """获取所有下载任务列表（按创建时间倒序）"""
    with download_lock:
        tasks = list(download_tasks.values())
        tasks.reverse()  # 最新的在前
    return jsonify({'code': 200, 'data': tasks})


@app.route('/api/download/clear', methods=['POST'])
def api_download_clear():
    """清除已完成的下载任务"""
    with download_lock:
        to_remove = [tid for tid, t in download_tasks.items()
                     if t['status'] in ('completed', 'failed', 'cancelled')]
        for tid in to_remove:
            del download_tasks[tid]
        save_tasks()
    try_start_queued()
    return jsonify({'code': 200, 'removed': len(to_remove)})


@app.route('/api/download/remove/<task_id>', methods=['POST'])
def api_download_remove(task_id):
    """删除单个等待或排队中的任务"""
    with download_lock:
        if task_id in download_tasks:
            t = download_tasks[task_id]
            if t['status'] in ('waiting', 'queued'):
                del download_tasks[task_id]
                save_tasks()
                return jsonify({'code': 200, 'message': '已删除'})
            else:
                return jsonify({'code': 400, 'message': '只能删除等待或排队中的任务'})
    return jsonify({'code': 404, 'message': '任务不存在'})


@app.route('/api/download/clear-queue', methods=['POST'])
def api_download_clear_queue():
    """清空所有等待和排队中的任务"""
    with download_lock:
        to_remove = [tid for tid, t in download_tasks.items()
                     if t['status'] in ('waiting', 'queued')]
        for tid in to_remove:
            del download_tasks[tid]
        save_tasks()
    return jsonify({'code': 200, 'removed': len(to_remove)})


@app.route('/api/download/cancel/<task_id>', methods=['POST'])
def api_download_cancel(task_id):
    """取消单个下载任务"""
    with download_lock:
        if task_id in download_tasks:
            t = download_tasks[task_id]
            if t['status'] in ('queued', 'downloading'):
                t['status'] = 'cancelled'
                save_tasks()
                return jsonify({'code': 200, 'message': '已取消'})
    return jsonify({'code': 404, 'message': '任务不存在或已结束'})


def _retry_segments(task_id, task, temp_dir, save_dir):
    """重试失败的分段"""
    seg_status = task.get('seg_status', [])
    task_url = task['url']
    segments, _ = parse_m3u8_segments(task_url)
    if len(segments) != len(seg_status):
        return False
    session = requests.Session()
    session.headers.update(HEADERS)
    ok = retry = 0
    for idx in range(len(segments)):
        if seg_status[idx] != 2:
            continue
        try:
            resp = session.get(segments[idx], timeout=120)
            if resp.status_code == 200:
                seg_path = os.path.join(temp_dir, f'seg_{idx:05d}.ts')
                with open(seg_path, 'wb') as f:
                    f.write(resp.content)
                seg_status[idx] = 1
                ok += 1
            else:
                retry += 1
        except:
            retry += 1
    with download_lock:
        if task_id in download_tasks:
            download_tasks[task_id]['seg_status'] = seg_status
            download_tasks[task_id]['progress'] = int(sum(1 for s in seg_status if s == 1) * 100 / max(len(seg_status), 1))
    return True


@app.route('/api/download/retry/<task_id>', methods=['POST'])
def api_download_retry(task_id):
    """重试失败的分段"""
    with download_lock:
        if task_id not in download_tasks:
            return jsonify({'code': 404, 'message': '任务不存在'})
        task = download_tasks[task_id]
        if task.get('status') in ('completed', 'downloading', 'queued'):
            return jsonify({'code': 400, 'message': '只有已失败的任务可以重试'})
        seg_status = task.get('seg_status', [])
        if not seg_status or not any(s == 2 for s in seg_status):
            return jsonify({'code': 400, 'message': '没有失败的分段'})
        task['status'] = 'downloading'
        save_tasks()

    save_dir = task['save_dir']
    temp_dir = os.path.join(save_dir, f'.tmp_{task_id}')
    os.makedirs(temp_dir, exist_ok=True)

    thread = threading.Thread(target=_retry_merge, args=(task_id, temp_dir), daemon=True)
    thread.start()
    return jsonify({'code': 200, 'message': '重试开始'})


def _retry_merge(task_id, temp_dir):
    """重试失败分段后重新合并"""
    with download_lock:
        if task_id not in download_tasks:
            return
        task = download_tasks[task_id]
        segments, _ = parse_m3u8_segments(task['url'])
        seg_status = task.get('seg_status', [])

    ok = _retry_segments(task_id, task, temp_dir, task['save_dir'])
    if not ok:
        update_task_progress(task_id, status='failed', error='重试失败', speed='')
        save_tasks()
        return

    # 重新合并
    total = len(segments)
    concat_file = os.path.join(temp_dir, 'concat.txt')
    with open(concat_file, 'w', encoding='utf-8') as f:
        for i in range(total):
            seg_path = os.path.join(temp_dir, f'seg_{i:05d}.ts')
            if os.path.exists(seg_path):
                f.write(f"file '{seg_path.replace(chr(92), '/')}'\n")

    output_path = task.get('output_file', '')
    if not output_path:
        update_task_progress(task_id, status='failed', error='无输出路径', speed='')
        return

    merge_cmd = [FFMPEG_PATH, '-f', 'concat', '-safe', '0',
                 '-i', concat_file, '-c', 'copy', '-y', output_path]
    merge_proc = subprocess.run(merge_cmd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=600)
    shutil.rmtree(temp_dir, ignore_errors=True)

    if merge_proc.returncode == 0:
        final_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        fail = sum(1 for s in seg_status if s == 2)
        update_task_progress(task_id, status='completed', progress=100, speed='',
                             total_bytes=final_size, file_size=format_size(final_size),
                             error=f'({fail}个分片失败)' if fail else '')
    else:
        update_task_progress(task_id, status='failed', error='重试后合并仍然失败', speed='')
    save_tasks()


@app.route('/api/download/force/<task_id>', methods=['POST'])
def api_download_force(task_id):
    """强制将已有分片合并（参考 m3u8-downloader 的强制下载现有片段）"""
    with download_lock:
        if task_id not in download_tasks:
            return jsonify({'code': 404, 'message': '任务不存在'})
        task = download_tasks[task_id]
        if task.get('status') in ('completed', 'merging'):
            return jsonify({'code': 400, 'message': '任务已结束或正在合并'})

    save_dir = task['save_dir']
    temp_dir = os.path.join(save_dir, f'.tmp_{task_id}')
    output_path = task.get('output_file', '')
    if not output_path:
        return jsonify({'code': 400, 'message': '无输出路径'})

    # 检查是否有已下载的分片
    if not os.path.exists(temp_dir):
        return jsonify({'code': 400, 'message': '没有可用的分段'})
    seg_files = [f for f in os.listdir(temp_dir) if f.endswith('.ts')]
    if not seg_files:
        return jsonify({'code': 400, 'message': '没有可用的分段'})

    update_task_progress(task_id, status='merging', progress=95)
    save_tasks()

    thread = threading.Thread(target=_do_force_merge, args=(task_id, temp_dir, output_path), daemon=True)
    thread.start()
    return jsonify({'code': 200, 'message': f'正在强制合并 {len(seg_files)} 个分段'})


def _do_force_merge(task_id, temp_dir, output_path):
    """强制合并已有分段"""
    seg_files = sorted([f for f in os.listdir(temp_dir) if f.endswith('.ts')],
                       key=lambda x: int(x.split('_')[1].split('.')[0]))
    concat_file = os.path.join(temp_dir, 'concat.txt')
    with open(concat_file, 'w', encoding='utf-8') as f:
        for sf in seg_files:
            f.write(f"file '{(os.path.join(temp_dir, sf)).replace(chr(92), '/')}'\n")
    merge_cmd = [FFMPEG_PATH, '-f', 'concat', '-safe', '0',
                 '-i', concat_file, '-c', 'copy', '-y', output_path]
    merge_proc = subprocess.run(merge_cmd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=600)
    shutil.rmtree(temp_dir, ignore_errors=True)
    if merge_proc.returncode == 0:
        final_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        update_task_progress(task_id, status='completed', progress=100, speed='',
                             total_bytes=final_size, file_size=format_size(final_size))
    else:
        update_task_progress(task_id, status='failed', error='强制合并失败', speed='')
    save_tasks()


@app.route('/api/download/concurrent', methods=['GET', 'POST'])
def api_concurrent():
    """获取/设置并发任务数"""
    global DEFAULT_MAX_CONCURRENT
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        val = int(data.get('limit', 3))
        if 1 <= val <= 20:
            DEFAULT_MAX_CONCURRENT = val
            return jsonify({'code': 200, 'limit': val})
        return jsonify({'code': 400, 'message': '范围 1-20'})
    return jsonify({'code': 200, 'limit': DEFAULT_MAX_CONCURRENT})


@app.route('/api/download/by-status')
def api_download_by_status():
    """按状态分类获取任务列表（支持逗号分隔多个状态）"""
    status_filter = request.args.get('status', '')  # e.g. downloading, waiting
    with download_lock:
        all_tasks = list(download_tasks.values())
        all_tasks.reverse()
    if status_filter:
        statuses = [s.strip() for s in status_filter.split(',') if s.strip()]
        filtered = [t for t in all_tasks if t['status'] in statuses]
    else:
        filtered = all_tasks
    return jsonify({'code': 200, 'data': filtered})


# ============================================
# 下载管理页面
# ============================================

@app.route('/downloads')
def downloads_page():
    """独立下载管理页面"""
    return render_template('downloads.html')


# ============================================
# 入口
# ============================================
if __name__ == '__main__':
    print("=" * 50)
    print("  m3u8 Search Tool (frontend pagination + download)")
    print(f"  target: {TARGET_BASE_URL}")
    print(f"  local:  http://127.0.0.1:5000")
    print(f"  lan:    http://192.168.70.172:5000")
    print("=" * 50)
    # debug=False：避免 Flask 开发服务器的线程问题导致 download_lock 卡死
    app.run(debug=False, host='0.0.0.0', port=5000)
