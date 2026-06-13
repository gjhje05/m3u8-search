# ============================================
# m3u8 Search Tool - Python Backend (rewritten v2)
# Features: search, m3u8 parsing, multi-threaded download,
#           task scheduler, ffmpeg merge, Range download
# Run: python app.py
# Access: http://0.0.0.0:5000
# ============================================

import os
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')

import re
import json
import time
import uuid
import queue
import shutil
import logging
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import urllib.parse
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, render_template


logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][PID:%(process)d] %(message)s',
    datefmt='%H:%M:%S',
    force=True
)
log = logging.getLogger("m3u8")

app = Flask(__name__)

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

DEFAULT_THREAD_COUNT = 8
MAX_THREAD_COUNT = 36
MIN_THREAD_COUNT = 4
DEFAULT_MAX_CONCURRENT = 3
MAX_CONCURRENT_LIMIT = 20
SCHEDULER_INTERVAL = 3

TIMEOUT_CONNECT = 15
TIMEOUT_READ = 60
TIMEOUT_MERGE = 600



def sanitize_filename(name):
    if not name:
        return 'unnamed'
    return re.sub(r'[\\/*?:"<>|]', '_', name).strip()


def format_size(bytes_val):
    if bytes_val < 0:
        bytes_val = 0
    if bytes_val < 1024:
        return f'{bytes_val}B'
    elif bytes_val < 1024 * 1024:
        return f'{bytes_val / 1024:.0f}KB'
    elif bytes_val < 1024 * 1024 * 1024:
        return f'{bytes_val / 1024 / 1024:.1f}MB'
    else:
        return f'{bytes_val / 1024 / 1024 / 1024:.2f}GB'


def format_speed(bps):
    if bps < 0:
        bps = 0
    if bps < 1024:
        return f"{bps:.0f} B/s"
    elif bps < 1024 * 1024:
        return f"{bps/1024:.0f} KB/s"
    else:
        return f"{bps/1024/1024:.1f} MB/s"


def resolve_url(base_url, path):
    path = path.strip()
    if not path:
        return base_url
    if path.startswith('http://') or path.startswith('https://'):
        return path
    if path.startswith('//'):
        return 'https:' + path
    base = base_url.rstrip('/') + '/'
    return urllib.parse.urljoin(base, path)


def get_default_save_dir():
    return os.path.join(os.path.expanduser('~'), 'Downloads', 'm3u8_downloads')


def get_save_dir_with_sub(save_dir, sub_dir):
    if sub_dir:
        safe_sub = sanitize_filename(sub_dir)[:50]
        return os.path.join(save_dir, safe_sub)
    return save_dir


def find_ffmpeg():
    candidates = []
    try:
        project_dir = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(project_dir, 'ffmpeg.exe'))
    except Exception:
        pass
    try:
        meipass = sys._MEIPASS
        candidates.append(os.path.join(meipass, 'ffmpeg.exe'))
        candidates.append(os.path.join(meipass, 'bin', 'ffmpeg.exe'))
    except AttributeError:
        pass
    candidates.extend([
        r'C:\ffmpeg\bin\ffmpeg.exe',
        r'C:\Program Files\ffmpeg\bin\ffmpeg.exe',
        r'C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe',
    ])
    for path in candidates:
        if path and os.path.isfile(path):
            log.info(f"ffmpeg found at: {path}")
            return path
    ff = shutil.which('ffmpeg')
    if ff:
        log.info(f"ffmpeg found via PATH: {ff}")
        return ff
    log.warning("ffmpeg not found in any location")
    return None


FFMPEG_PATH = find_ffmpeg()


def check_ffmpeg():
    if FFMPEG_PATH and os.path.isfile(FFMPEG_PATH):
        return True, f"ffmpeg: {FFMPEG_PATH}"
    return False, "ffmpeg not found, download from https://ffmpeg.org/download.html"


_m3u8_cache = {}
_m3u8_cache_lock = threading.RLock()
_M3U8_CACHE_TTL = 60


def detect_segment_format(m3u8_content):
    lines = m3u8_content.split(chr(10))
    if '#EXT-X-MAP' in m3u8_content:
        return 'fmp4'
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#'):
            if '.m4s' in line.lower():
                return 'm4s'
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#'):
            if '.ts' in line.lower():
                return 'ts'
    return 'ts'


def parse_m3u8_recursive(url, depth=0, max_depth=10):
    if depth > max_depth:
        log.warning(f"[parse_m3u8] max recursion depth {max_depth}: {url[:50]}...")
        return [], 0, 'ts'
    now = time.time()
    with _m3u8_cache_lock:
        cached = _m3u8_cache.get(url)
        if cached and now - cached[0] < _M3U8_CACHE_TTL:
            return cached[1], cached[2], cached[3]
    try:
        session = requests.Session()
        session.verify = False
        session.headers.update(HEADERS)
        session.headers['Referer'] = url.rsplit('/', 1)[0] + '/'
        resp = session.get(url, timeout=(TIMEOUT_CONNECT, TIMEOUT_READ))
        resp.encoding = 'utf-8'
        content = resp.text
        if '#EXT-X-STREAM-INF' in content or '#EXT-X-I-FRAME-STREAM-INF' in content:
            for line in content.split(chr(10)):
                line = line.strip()
                if line and not line.startswith('#'):
                    sub_url = resolve_url(url, line)
                    return parse_m3u8_recursive(sub_url, depth + 1, max_depth)
            return [], 0, 'ts'
        segments = []
        total_duration = 0.0
        for line in content.split(chr(10)):
            line = line.strip()
            if not line:
                continue
            if line.startswith('#EXTINF:'):
                m = re.search(r'#EXTINF:\s*([\d.]+)', line)
                if m:
                    total_duration += float(m.group(1))
            elif not line.startswith('#'):
                seg_url = resolve_url(url, line)
                segments.append(seg_url)
        if not segments:
            return [], 0, 'ts'
        fmt = detect_segment_format(content)
        with _m3u8_cache_lock:
            _m3u8_cache[url] = (time.time(), list(segments), total_duration, fmt)
        log.info(f"[parse_m3u8] {len(segments)} segs, {total_duration:.1f}s, format={fmt}")
        return segments, total_duration, fmt
    except Exception as e:
        log.warning(f"[parse_m3u8] error: {e}")
        return [], 0, 'ts'


def invalidate_m3u8_cache(url):
    with _m3u8_cache_lock:
        _m3u8_cache.pop(url, None)


def is_m3u8_url(url, timeout=5):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, verify=False, stream=True)
        chunk = resp.raw.read(1024)
        return b'#EXTM3U' in chunk
    except Exception:
        return False


def adaptive_thread_count(base=8):
    count = base
    count = max(MIN_THREAD_COUNT, min(count, MAX_THREAD_COUNT))
    return count


def download_with_retry(session, url, timeout=TIMEOUT_READ, max_retries=3):
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(url, timeout=(TIMEOUT_CONNECT, timeout))
            if resp.status_code == 200:
                return resp.content, 200, None
            elif resp.status_code in (404, 403, 503, 410):
                return None, resp.status_code, f"HTTP {resp.status_code}"
            else:
                if attempt < max_retries:
                    delay = 2 ** attempt
                    log.debug(f"[retry] {attempt}/{max_retries} HTTP {resp.status_code}, waiting {delay}s")
                    time.sleep(delay)
                else:
                    return None, resp.status_code, f"HTTP {resp.status_code} ({attempt})"
        except requests.Timeout as e:
            last_exc = e
            if attempt < max_retries:
                delay = 2 ** attempt
                log.debug(f"[retry] {attempt}/{max_retries} timeout, waiting {delay}s")
                time.sleep(delay)
            else:
                return None, 0, f"Timeout after {max_retries} retries"
        except requests.ConnectionError as e:
            last_exc = e
            if attempt < max_retries:
                delay = 2 ** attempt
                log.debug(f"[retry] {attempt}/{max_retries} conn err, waiting {delay}s")
                time.sleep(delay)
            else:
                return None, 0, f"ConnectionError after {max_retries} retries"
        except Exception as e:
            last_exc = e
            if attempt < max_retries:
                delay = 2 ** attempt
                log.debug(f"[retry] {attempt}/{max_retries} {type(e).__name__}, waiting {delay}s")
                time.sleep(delay)
            else:
                return None, 0, f"{type(e).__name__}: {str(e)[:80]}"
    return None, 0, str(last_exc)[:80] if last_exc else "Unknown"


def download_segments_parallel(task_id, segments, m3u8_url, temp_dir, task_ref):
    total_segs = len(segments)
    if total_segs == 0:
        return 0, 0, []
    seg_lock = threading.Lock()
    completed_count = [0]
    downloaded_bytes = [0]
    seg_status = [0] * total_segs
    start_time = time.time()
    thread_count = adaptive_thread_count(task_ref.get('thread_count', DEFAULT_THREAD_COUNT))
    segment_ref = m3u8_url.rsplit('/', 1)[0] + '/'

    def dl_one(index, seg_url):
        session = requests.Session()
        session.verify = False
        session.headers.update(HEADERS)
        session.headers['Referer'] = segment_ref
        data, sc, err = download_with_retry(session, seg_url)
        if data is not None:
            if '.m4s' in seg_url:
                file_ext = '.m4s'
            else:
                file_ext = '.ts'
            out_path = os.path.join(temp_dir, f'seg_{index:05d}{file_ext}')
            try:
                with open(out_path, 'wb') as f:
                    f.write(data)
            except OSError as e:
                log.error(f"[{task_id}] write error seg_{index}: {e}")
                with seg_lock:
                    seg_status[index] = 2
                return False
            with seg_lock:
                seg_status[index] = 1
                completed_count[0] += 1
                downloaded_bytes[0] += len(data)
            return True
        log.info(f"[{task_id}] seg {index} failed ({sc or err}), refreshing m3u8...")
        try:
            fresh_segs, _, _ = parse_m3u8_recursive(m3u8_url)
            if fresh_segs and index < len(fresh_segs):
                fresh_url = fresh_segs[index]
                if fresh_url != seg_url:
                    data2, sc2, err2 = download_with_retry(session, fresh_url)
                    if data2 is not None:
                        if '.m4s' in fresh_url:
                            file_ext = '.m4s'
                        else:
                            file_ext = '.ts'
                        out_path = os.path.join(temp_dir, f'seg_{index:05d}{file_ext}')
                        with open(out_path, 'wb') as f:
                            f.write(data2)
                        with seg_lock:
                            seg_status[index] = 1
                            completed_count[0] += 1
                            downloaded_bytes[0] += len(data2)
                        log.info(f"[{task_id}] seg {index} recovered via fresh URL")
                        return True
        except Exception as e2:
            log.warning(f"[{task_id}] seg {index} retry refresh error: {e2}")
        with seg_lock:
            seg_status[index] = 2
        return False

    futures = {}
    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        for idx, seg_url in enumerate(segments):
            futures[executor.submit(dl_one, idx, seg_url)] = idx
        for future in as_completed(futures):
            cancelled = False
            with download_lock:
                if task_id in download_tasks and download_tasks[task_id].get('status') == 'cancelled':
                    cancelled = True
            if cancelled:
                executor.shutdown(wait=False, cancel_futures=True)
                break
            with seg_lock:
                done = completed_count[0]
                tb = downloaded_bytes[0]
            pct = int(done * 100 / total_segs) if total_segs > 0 else 0
            elapsed = time.time() - start_time
            speed_bps = tb / elapsed if elapsed > 0.5 else 0
            update_task_progress(task_id,
                progress=pct,
                speed=format_speed(speed_bps) if speed_bps > 0 else '',
                total_bytes=tb,
                file_size=format_size(tb),
                seg_status=list(seg_status)
            )
    with seg_lock:
        return completed_count[0], downloaded_bytes[0], list(seg_status)


def get_init_segment(m3u8_url, temp_dir):
    """
    For fmp4 streams, download the #EXT-X-MAP init segment.
    Returns path to the init segment file, or None.
    """
    try:
        session = requests.Session()
        session.verify = False
        session.headers.update(HEADERS)
        resp = session.get(m3u8_url, timeout=(TIMEOUT_CONNECT, TIMEOUT_READ))
        resp.encoding = 'utf-8'
        content = resp.text
        init_uri = None
        for line in content.split(chr(10)):
            line = line.strip()
            if line.startswith('#EXT-X-MAP:'):
                m = re.search(r'URI="([^"]+)"', line)
                if m:
                    init_uri = m.group(1)
                    break
        if not init_uri:
            return None
        init_url = resolve_url(m3u8_url, init_uri)
        data, sc, err = download_with_retry(session, init_url)
        if data is None or len(data) < 32:
            log.warning(f"[init] download failed or too small: {err}")
            return None
        init_path = os.path.join(temp_dir, 'seg_init.dat')
        with open(init_path, 'wb') as f:
            f.write(data)
        log.info(f"[init] downloaded {len(data)} bytes from {init_uri}")
        return init_path
    except Exception as e:
        log.warning(f"[init] error: {e}")
        return None


def merge_segments(task_id, temp_dir, output_path, seg_status, total_segs, seg_ext='.ts', m3u8_url=None):
    if not FFMPEG_PATH:
        return False, "ffmpeg not installed"
    try:
        concat_file = os.path.join(temp_dir, 'concat.txt')
        seg_count = 0
        with open(concat_file, 'w', encoding='utf-8') as f:
            # For fmp4, prepend the init segment first
            init_path = None
            if seg_ext == '.m4s' and m3u8_url:
                init_path = get_init_segment(m3u8_url, temp_dir)
            if init_path and os.path.exists(init_path):
                f.write(f"file '{os.path.abspath(init_path).replace(chr(92), '/')}'" + chr(10))
                seg_count += 1
            for i in range(total_segs):
                found = False
                for ext in ('.ts', '.m4s', '.dat'):
                    seg_path = os.path.join(temp_dir, f'seg_{i:05d}{ext}')
                    if os.path.exists(seg_path):
                        # Validate segment size: skip if too small (likely error page)
                        sz = os.path.getsize(seg_path)
                        if sz < 100:
                            continue
                        f.write(f"file '{os.path.abspath(seg_path).replace(chr(92), '/')}'" + chr(10))
                        seg_count += 1
                        found = True
                        break
        if seg_count == 0:
            return False, "no valid segments to merge"
        log.info(f"[{task_id}] merging {seg_count}/{total_segs} segs -> {os.path.basename(output_path)}")
        update_task_progress(task_id, status='merging', progress=95, speed='')
        merge_cmd = [
            FFMPEG_PATH, '-f', 'concat', '-safe', '0',
            '-i', concat_file, '-c', 'copy', '-y', output_path
        ]
        merge_proc = subprocess.run(
            merge_cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=TIMEOUT_MERGE
        )
        if merge_proc.returncode == 0:
            log.info(f"[{task_id}] merge completed")
            return True, None
        else:
            err = merge_proc.stderr[-300:] if merge_proc.stderr else "ffmpeg error"
            err_lines = [l for l in err.split(chr(10)) if 'error' in l.lower() or 'invalid' in l.lower()]
            short = (err_lines[0][:120] if err_lines else err[:120])
            return False, f"merge failed: {short}"
    except subprocess.TimeoutExpired:
        return False, f"merge timeout ({TIMEOUT_MERGE}s)"
    except Exception as e:
        return False, f"merge exception: {str(e)[:80]}"


def download_ffmpeg_direct(task_id, url, output_path):
    if not FFMPEG_PATH:
        update_task_progress(task_id, status='failed', error='ffmpeg not found', progress=0)
        save_tasks()
        scheduler_notify_completed(task_id)
        return
    update_task_progress(task_id, status='downloading', progress=0, speed='')
    save_tasks()
    try:
        ff_ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0'
        ff_ref = url.rsplit('/', 1)[0] + '/'
        cmd = [
            FFMPEG_PATH,
            '-headers', f'Referer: {ff_ref}\r\nUser-Agent: {ff_ua}\r\n',
            '-tls_verify', '0',
            '-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '5',
            '-nostdin',
            '-i', url,
            '-c', 'copy', '-bsf:a', 'aac_adtstoasc', '-y',
            '-progress', 'pipe:1', output_path
        ]
        start_time = time.time()
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, encoding='utf-8', errors='replace'
        )
        for line in iter(process.stdout.readline, ''):
            line = line.strip()
            if not line:
                continue
            if line.startswith('progress=') and 'end' in line:
                update_task_progress(task_id, progress=100, speed='')
                break
            if line.startswith('out_time_us='):
                try:
                    us = int(line.split('=')[1])
                    sec = us / 1_000_000
                    pct = min(int(sec / 30 * 90), 90) if sec < 30 else min(int(sec * 90 / (sec + 30)), 90)
                    if pct > 0:
                        update_task_progress(task_id, progress=pct)
                except Exception:
                    pass
            if line.startswith('total_size='):
                try:
                    tb = int(line.split('=')[1])
                    elapsed = time.time() - start_time
                    if elapsed > 1:
                        update_task_progress(task_id,
                            speed=format_speed(tb / elapsed),
                            file_size=format_size(tb))
                except Exception:
                    pass
        process.wait()
        if process.returncode == 0:
            final_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
            update_task_progress(task_id, status='completed', progress=100, speed='',
                                 file_size=format_size(final_size), total_bytes=final_size)
        else:
            if os.path.exists(output_path) and os.path.getsize(output_path) > 1024 * 1024:
                final_size = os.path.getsize(output_path)
                update_task_progress(task_id, status='completed', progress=100, speed='',
                                     file_size=format_size(final_size), total_bytes=final_size,
                                     error=f'ffmpeg exit {process.returncode} but file may be valid')
            else:
                update_task_progress(task_id, status='failed',
                                     error=f'ffmpeg exit {process.returncode}', progress=0)
    except FileNotFoundError:
        update_task_progress(task_id, status='failed', error='ffmpeg not found', progress=0)
    except Exception as e:
        update_task_progress(task_id, status='failed', error=f'ffmpeg: {str(e)[:80]}', progress=0)
    save_tasks()
    scheduler_notify_completed(task_id)


def download_single_file_range(task_id, url, output_path):
    update_task_progress(task_id, status='downloading', progress=0, speed='')
    save_tasks()
    try:
        session = requests.Session()
        session.verify = False
        session.headers.update(HEADERS)
        head_resp = session.head(url, timeout=(TIMEOUT_CONNECT, TIMEOUT_READ))
        total_size = int(head_resp.headers.get('Content-Length', 0))
        accept_ranges = head_resp.headers.get('Accept-Ranges', '')
        if total_size <= 0 or 'bytes' not in accept_ranges.lower():
            log.info(f"[{task_id}] fallback to normal download (no Range support)")
            resp = session.get(url, timeout=(TIMEOUT_CONNECT, TIMEOUT_READ * 10), stream=True)
            with open(output_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            final_size = os.path.getsize(output_path)
            update_task_progress(task_id, status='completed', progress=100, speed='',
                                 file_size=format_size(final_size), total_bytes=final_size)
            save_tasks()
            scheduler_notify_completed(task_id)
            return
        thread_count = adaptive_thread_count(DEFAULT_THREAD_COUNT)
        chunk_size = max(1024 * 1024, total_size // (thread_count * 2))
        chunks = []
        start = 0
        while start < total_size:
            end = min(start + chunk_size - 1, total_size - 1)
            chunks.append((start, end))
            start = end + 1
        log.info(f"[{task_id}] Range: {total_size} bytes, {len(chunks)} chunks, {thread_count} threads")
        dl_lock = threading.Lock()
        results = {}
        start_time = time.time()
        completed_chunks = [0]

        def dl_chunk(chunk_idx, byte_start, byte_end):
            s = requests.Session()
            s.verify = False
            s.headers.update(HEADERS)
            s.headers['Range'] = f'bytes={byte_start}-{byte_end}'
            try:
                resp = s.get(url, timeout=(TIMEOUT_CONNECT, TIMEOUT_READ * 2))
                if resp.status_code in (206, 200):
                    with dl_lock:
                        results[chunk_idx] = (byte_start, resp.content)
                        completed_chunks[0] += 1
                        done = completed_chunks[0]
                        tb = sum(len(v[1]) for v in results.values())
                        elapsed = time.time() - start_time
                        speed_bps = tb / elapsed if elapsed > 0.5 else 0
                        pct = int(done * 100 / len(chunks))
                        update_task_progress(task_id, progress=pct,
                            speed=format_speed(speed_bps) if speed_bps > 0 else '',
                            total_bytes=tb, file_size=format_size(tb))
                    return True
            except Exception:
                pass
            with dl_lock:
                completed_chunks[0] += 1
            return False

        futures = {}
        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            for idx, (s, e) in enumerate(chunks):
                futures[executor.submit(dl_chunk, idx, s, e)] = idx
            for f in as_completed(futures):
                with download_lock:
                    if task_id in download_tasks and download_tasks[task_id].get('status') == 'cancelled':
                        executor.shutdown(wait=False, cancel_futures=True)
                        break
        with download_lock:
            if task_id in download_tasks and download_tasks[task_id].get('status') == 'cancelled':
                save_tasks()
                return
        sorted_results = sorted(results.items(), key=lambda x: x[0])
        written = 0
        with open(output_path, 'wb') as f:
            for idx, (byte_start, data) in sorted_results:
                f.write(data)
                written += len(data)
        if written >= total_size * 0.95:
            update_task_progress(task_id, status='completed', progress=100, speed='',
                file_size=format_size(written), total_bytes=written,
                error=f'({total_size - written} bytes missing)' if written < total_size else '')
            log.info(f"[{task_id}] Range download: {format_size(written)}")
        else:
            update_task_progress(task_id, status='failed',
                error=f'incomplete: {format_size(written)}/{format_size(total_size)}', speed='',
                total_bytes=written, file_size=format_size(written))
    except Exception as e:
        log.warning(f"[{task_id}] single file download error: {e}")
        update_task_progress(task_id, status='failed', error=f'download error: {str(e)[:80]}')
    save_tasks()
    scheduler_notify_completed(task_id)


def download_worker(task_id, url, title, save_dir, episode_num=0, thread_count=None):
    temp_dir = None
    try:
        os.makedirs(save_dir, exist_ok=True)
        safe_title = sanitize_filename(title)[:80]
        if episode_num > 0:
            filename = f"{safe_title}_E{episode_num:02d}.mp4"
        else:
            filename = f"{safe_title}.mp4"
        output_path = os.path.join(save_dir, filename)
        counter = 1
        base_name = filename[:-4]
        while os.path.exists(output_path):
            output_path = os.path.join(save_dir, f"{base_name}_{counter}.mp4")
            counter += 1
        update_task_progress(task_id,
            status='downloading', output_file=output_path,
            started_at=datetime.now().isoformat(), speed='', progress=0,
            total_bytes=0, file_size='')
        save_tasks()
        is_m3u8 = is_m3u8_url(url)
        if not is_m3u8:
            log.info(f"[{task_id}] single file (non-m3u8): {url[:60]}...")
            download_single_file_range(task_id, url, output_path)
            return
        log.info(f"[{task_id}] parsing m3u8: {url[:60]}...")
        segments, total_dur, seg_format = parse_m3u8_recursive(url)
        total_segs = len(segments)
        if total_segs == 0:
            log.info(f"[{task_id}] no segments, fallback to ffmpeg direct")
            download_ffmpeg_direct(task_id, url, output_path)
            return
        log.info(f"[{task_id}] {total_segs} segs, {total_dur:.1f}s, format={seg_format}")
        if total_segs < 3:
            log.info(f"[{task_id}] too few segs ({total_segs}), fallback to ffmpeg direct")
            download_ffmpeg_direct(task_id, url, output_path)
            return
        temp_dir = os.path.join(save_dir, f'.tmp_{task_id}')
        os.makedirs(temp_dir, exist_ok=True)
        seg_ext = '.m4s' if seg_format in ('m4s', 'fmp4') else '.ts'
        ok_segs, total_bytes, seg_status = download_segments_parallel(
            task_id, segments, url, temp_dir,
            {'thread_count': thread_count or DEFAULT_THREAD_COUNT}
        )
        with download_lock:
            if task_id in download_tasks and download_tasks[task_id].get('status') == 'cancelled':
                shutil.rmtree(temp_dir, ignore_errors=True)
                save_tasks()
                scheduler_notify_completed(task_id)
                return
        if ok_segs == 0:
            log.warning(f"[{task_id}] all segments failed, fallback to ffmpeg")
            shutil.rmtree(temp_dir, ignore_errors=True)
            download_ffmpeg_direct(task_id, url, output_path)
            return
        merge_ok, merge_err = merge_segments(task_id, temp_dir, output_path, seg_status, total_segs, seg_ext, url)
        shutil.rmtree(temp_dir, ignore_errors=True)
        if merge_ok:
            fail_count = sum(1 for s in seg_status if s == 2)
            final_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
            update_task_progress(task_id, status='completed', progress=100, speed='',
                total_bytes=final_size, file_size=format_size(final_size),
                error=f'({fail_count} failed segs ignored)' if fail_count else '')
            log.info(f"[{task_id}] completed: {format_size(final_size)}")
        else:
            if os.path.exists(output_path):
                os.remove(output_path)
            update_task_progress(task_id, status='failed', error=merge_err or 'merge failed', progress=0, speed='')
    except Exception as e:
        log.error(f"[{task_id}] unexpected error: {e}")
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        update_task_progress(task_id, status='failed', error=str(e)[:100])
    save_tasks()
    scheduler_notify_completed(task_id)


TASKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'download_tasks.json')

download_tasks = {}
download_lock = threading.RLock()

_scheduler_running = False
_scheduler_thread = None
_work_queue = queue.Queue()
_should_stop = threading.Event()


def save_tasks():
    try:
        with open(TASKS_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(download_tasks.values()), f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"[save_tasks] error: {e}")


def load_tasks():
    global download_tasks
    try:
        if os.path.exists(TASKS_FILE):
            with open(TASKS_FILE, 'r', encoding='utf-8') as f:
                tasks_list = json.load(f)
            download_tasks = {t['id']: t for t in tasks_list}
            reset_count = 0
            for t in download_tasks.values():
                if t.get('status') in ('downloading', 'queued', 'merging'):
                    t['status'] = 'waiting'
                    t['started_at'] = None
                    reset_count += 1
            log.info(f"[load_tasks] loaded {len(download_tasks)} tasks, reset {reset_count}")
    except Exception as e:
        log.warning(f"[load_tasks] error: {e}")


def update_task_progress(task_id, **kwargs):
    with download_lock:
        if task_id in download_tasks:
            for k, v in kwargs.items():
                download_tasks[task_id][k] = v


def count_active_tasks():
    with download_lock:
        return sum(1 for t in download_tasks.values()
                   if t.get('status') in ('queued', 'downloading', 'merging'))


def count_waiting_tasks():
    with download_lock:
        return sum(1 for t in download_tasks.values()
                   if t.get('status') == 'waiting')


def scheduler_notify_completed(task_id):
    _work_queue.put(('completed', task_id))


def start_scheduler():
    global _scheduler_running, _scheduler_thread
    if _scheduler_running:
        return
    _should_stop.clear()
    _scheduler_running = True
    _scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True, name='scheduler')
    _scheduler_thread.start()
    log.info("[scheduler] started")


def stop_scheduler():
    global _scheduler_running
    _scheduler_running = False
    _should_stop.set()
    log.info("[scheduler] stop requested")


def scheduler_loop():
    global _scheduler_running
    log.info(f"[scheduler] loop started (interval={SCHEDULER_INTERVAL}s)")
    while _scheduler_running and not _should_stop.is_set():
        try:
            _try_start_waiting_tasks()
            try:
                msg, tid = _work_queue.get(timeout=SCHEDULER_INTERVAL)
                if msg == 'completed' or msg == 'new_task':
                    _try_start_waiting_tasks()
            except queue.Empty:
                pass
        except Exception as e:
            log.warning(f"[scheduler] loop error: {e}")
            time.sleep(SCHEDULER_INTERVAL)
    log.info("[scheduler] loop stopped")


def _try_start_waiting_tasks():
    tid_to_start = None
    with download_lock:
        active = count_active_tasks()
        limit = DEFAULT_MAX_CONCURRENT
        if active >= limit:
            return
        for t in download_tasks.values():
            if t.get('status') == 'waiting':
                t['status'] = 'queued'
                tid_to_start = t['id']
                break
    if tid_to_start:
        with download_lock:
            t = download_tasks.get(tid_to_start)
        if t:
            log.info(f"[scheduler] start {tid_to_start}: {t['title'][:40]}")
            thread = threading.Thread(
                target=download_worker,
                args=(tid_to_start, t['url'], t['title'], t['save_dir'],
                      t.get('episode', 0), t.get('thread_count', DEFAULT_THREAD_COUNT)),
                daemon=True, name=f'dl-{tid_to_start}'
            )
            thread.start()
            save_tasks()


def _retry_failed_segments(task_id, temp_dir):
    with download_lock:
        if task_id not in download_tasks:
            return 0, 0, []
        task = download_tasks[task_id]
        seg_status = list(task.get('seg_status', []))
        url = task['url']
    if not seg_status or not any(s == 2 for s in seg_status):
        return 0, len(seg_status), seg_status
    segments, _, seg_format = parse_m3u8_recursive(url)
    if len(segments) != len(seg_status):
        seg_status = [0] * len(segments)
    total = len(segments)
    seg_ext = '.m4s' if seg_format in ('m4s', 'fmp4') else '.ts'
    os.makedirs(temp_dir, exist_ok=True)
    session = requests.Session()
    session.verify = False
    session.headers.update(HEADERS)
    session.headers['Referer'] = url.rsplit('/', 1)[0] + '/'
    success_count = 0
    for idx in range(total):
        if idx < len(seg_status) and seg_status[idx] != 2:
            continue
        data, sc, err = download_with_retry(session, segments[idx])
        if data is not None:
            seg_path = os.path.join(temp_dir, f'seg_{idx:05d}{seg_ext}')
            try:
                with open(seg_path, 'wb') as f:
                    f.write(data)
            except OSError:
                continue
            seg_status[idx] = 1
            success_count += 1
            log.info(f"[retry] seg {idx}/{total} recovered")
    with download_lock:
        if task_id in download_tasks:
            download_tasks[task_id]['seg_status'] = seg_status
            ok_count = sum(1 for s in seg_status if s == 1)
            download_tasks[task_id]['progress'] = int(ok_count * 100 / max(total, 1))
            save_tasks()
    return success_count, total, seg_status


def extract_m3u8_urls(dd_text):
    if not dd_text:
        return []
    return re.findall(r'(https?://[^\s$]+?\.m3u8)', dd_text)


def parse_search_page(html):
    videos = []
    page_info = None
    soup = BeautifulSoup(html, 'lxml')
    tip = soup.select_one('.page_tip')
    if tip:
        text = tip.get_text(strip=True)
        m = re.search(r'共(\d+)条数据,?\s*当前(\d+)/(\d+)页', text)
        if m:
            page_info = {
                'total_items': int(m.group(1)),
                'current': int(m.group(2)),
                'total': int(m.group(3))
            }
    show_list = soup.find('ul', class_='show-list')
    if not show_list:
        return videos, page_info
    for li in show_list.find_all('li', recursive=False):
        video = {}
        h2 = li.find('h2')
        if h2:
            a = h2.find('a')
            video['title'] = (a.get_text(strip=True) if a else h2.get_text(strip=True))
        else:
            video['title'] = '未知标题'
        img_link = li.find('a', class_='play-img')
        if img_link and img_link.get('href'):
            p = img_link['href']
            video['detail_url'] = TARGET_BASE_URL + p if p.startswith('/') else p
            img = img_link.find('img')
            img_src = img['src'] if img and img.get('src') else ''
            video['cover_img'] = (TARGET_BASE_URL + img_src) if img_src.startswith('/') else img_src
        else:
            video['detail_url'] = ''
            video['cover_img'] = ''
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


def get_page_pattern(html):
    soup = BeautifulSoup(html, 'lxml')
    link = soup.select_one('a.page_link[href*="/page/2/"]')
    if link and link.get('href'):
        href = link['href']
        pattern = re.sub(r'/page/\d+/', '/page/{page}/', href)
        return TARGET_BASE_URL + pattern
    return None


def fetch_page(keyword, page_num):
    encoded = urllib.parse.quote(keyword, encoding='utf-8')
    if page_num == 1:
        url = f"{TARGET_SEARCH_URL}?wd={encoded}"
    else:
        try:
            r1 = requests.get(f"{TARGET_SEARCH_URL}?wd={encoded}", headers=HEADERS, timeout=15)
            r1.encoding = 'utf-8'
            if r1.status_code != 200:
                return None, None, None, None
            html1 = r1.text
            _, page_info = parse_search_page(html1)
            pattern = get_page_pattern(html1)
            if not pattern or not page_info or page_info.get('total', 1) < page_num:
                return None, page_info, None, None
            url = pattern.replace('{page}', str(page_num))
        except Exception as e:
            log.error(f"get page pattern failed: {e}")
            return None, None, None, None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        if resp.status_code != 200:
            return None, None, None, None
        videos, page_info = parse_search_page(resp.text)
        return videos, page_info, url, (resp.text if page_num == 1 else None)
    except Exception as e:
        log.error(f"request failed: {e}")
        return None, None, None, None


@app.route('/')
def index():
    return render_template('index.html', target_url=TARGET_BASE_URL)


@app.route('/api/search')
def api_search():
    keyword = request.args.get('wd', '').strip()
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1
    if page < 1:
        page = 1
    if not keyword:
        return jsonify({"code": 400, "message": "请输入搜索关键词", "data": [], "total": 0, "page_info": None})
    safe_kw = keyword.encode('ascii', 'backslashreplace').decode('ascii')
    log.info(f"search: {safe_kw} page={page}")
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


@app.route('/api/check-server')
def api_check_server():
    try:
        resp = requests.get(TARGET_BASE_URL, headers=HEADERS, timeout=5)
        return jsonify({"online": resp.status_code == 200, "status_code": resp.status_code, "server": TARGET_BASE_URL})
    except Exception as e:
        return jsonify({"online": False, "error": str(e), "server": TARGET_BASE_URL})


@app.route('/api/download', methods=['POST'])
def api_download():
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    title = (data.get('title') or '未知视频').strip()
    save_dir = (data.get('save_dir') or get_default_save_dir()).strip()
    episode = int(data.get('episode', 0))
    sub_dir = (data.get('sub_dir') or '').strip()
    thread_count = int(data.get('thread_count', DEFAULT_THREAD_COUNT))
    ffmpeg_ok, ffmpeg_msg = check_ffmpeg()
    if not ffmpeg_ok:
        log.warning(f"[api_download] {ffmpeg_msg}")
    if not url:
        return jsonify({'code': 400, 'message': '缺少下载地址'})
    actual_save_dir = get_save_dir_with_sub(save_dir, sub_dir)
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
            'status': 'waiting',
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
    _work_queue.put(('new_task', None))
    return jsonify({
        'code': 200,
        'task_id': task_id,
        'message': f'任务已加入队列: {title}',
        'status': 'waiting'
    })


@app.route('/api/download/batch', methods=['POST'])
def api_download_batch():
    data = request.get_json(silent=True) or {}
    items = data.get('items') or []
    save_dir = (data.get('save_dir') or get_default_save_dir()).strip()
    start_episode = int(data.get('start_episode', 1))
    sub_dir = (data.get('sub_dir') or '').strip()
    thread_count = int(data.get('thread_count', DEFAULT_THREAD_COUNT))
    if not items:
        return jsonify({'code': 400, 'message': '没有要下载的项目'})
    actual_save_dir = get_save_dir_with_sub(save_dir, sub_dir)
    task_ids = []
    now_iso = datetime.now().isoformat()
    with download_lock:
        for idx, item in enumerate(items):
            url = (item.get('url') or '').strip()
            title = (item.get('title') or '未知视频').strip()
            ep = start_episode + idx if start_episode > 0 else 0
            if not url:
                continue
            tid = str(uuid.uuid4())[:8]
            download_tasks[tid] = {
                'id': tid, 'url': url, 'title': title,
                'save_dir': actual_save_dir, 'episode': ep, 'sub_dir': sub_dir,
                'status': 'waiting', 'progress': 0,
                'created_at': now_iso, 'started_at': None,
                'output_file': None, 'error': None,
                'speed': '', 'total_bytes': 0, 'file_size': '',
                'seg_status': [], 'thread_count': thread_count
            }
            task_ids.append(tid)
        save_tasks()
    _work_queue.put(('new_task', None))
    return jsonify({'code': 200, 'task_ids': task_ids, 'count': len(task_ids), 'message': f'已提交 {len(task_ids)} 个下载任务'})


@app.route('/api/download/list')
def api_download_list():
    with download_lock:
        tasks = list(download_tasks.values())
        tasks.reverse()
    return jsonify({'code': 200, 'data': tasks})


@app.route('/api/download/clear', methods=['POST'])
def api_download_clear():
    with download_lock:
        to_remove = [tid for tid, t in download_tasks.items() if t['status'] in ('completed', 'failed', 'cancelled')]
        for tid in to_remove:
            del download_tasks[tid]
        save_tasks()
    return jsonify({'code': 200, 'removed': len(to_remove)})


@app.route('/api/download/remove/<task_id>', methods=['POST'])
def api_download_remove(task_id):
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
    with download_lock:
        to_remove = [tid for tid, t in download_tasks.items() if t['status'] in ('waiting', 'queued')]
        for tid in to_remove:
            del download_tasks[tid]
        save_tasks()
    return jsonify({'code': 200, 'removed': len(to_remove)})


@app.route('/api/download/cancel/<task_id>', methods=['POST'])
def api_download_cancel(task_id):
    with download_lock:
        if task_id in download_tasks:
            t = download_tasks[task_id]
            if t['status'] in ('queued', 'downloading', 'waiting'):
                t['status'] = 'cancelled'
                save_tasks()
                return jsonify({'code': 200, 'message': '已取消'})
            return jsonify({'code': 400, 'message': '任务已结束'})
    return jsonify({'code': 404, 'message': '任务不存在'})


@app.route('/api/download/retry/<task_id>', methods=['POST'])
def api_download_retry(task_id):
    with download_lock:
        if task_id not in download_tasks:
            return jsonify({'code': 404, 'message': '任务不存在'})
        task = download_tasks[task_id]
        if task['status'] not in ('failed', 'completed'):
            return jsonify({'code': 400, 'message': '只有已失败或已完成的可以重试'})
        seg_status = task.get('seg_status', [])
        if not seg_status or not any(s == 2 for s in seg_status):
            return jsonify({'code': 400, 'message': '没有失败的分段'})
        task['status'] = 'downloading'
        save_tasks()
    save_dir = task['save_dir']
    temp_dir = os.path.join(save_dir, f'.tmp_{task_id}')
    output_path = task.get('output_file', '')
    if not output_path:
        return jsonify({'code': 400, 'message': '缺少输出路径'})
    thread = threading.Thread(
        target=_retry_then_merge,
        args=(task_id, temp_dir, output_path),
        daemon=True, name=f'retry-{task_id}'
    )
    thread.start()
    return jsonify({'code': 200, 'message': '重试开始'})


def _retry_then_merge(task_id, temp_dir, output_path):
    try:
        retried, total, seg_status = _retry_failed_segments(task_id, temp_dir)
        log.info(f"[retry] {task_id}: recovered {retried}/{total} segments")
        with download_lock:
            if task_id not in download_tasks:
                return
            task = download_tasks[task_id]
            seg_status = task.get('seg_status', [])
        if not seg_status or sum(1 for s in seg_status if s == 1) == 0:
            update_task_progress(task_id, status='failed', error='重试后仍然无成功分段', speed='')
            save_tasks()
            scheduler_notify_completed(task_id)
            return
        total = len(seg_status)
        concat_file = os.path.join(temp_dir, 'concat.txt')
        with open(concat_file, 'w', encoding='utf-8') as f:
            for i in range(total):
                for ext in ('.ts', '.m4s'):
                    seg_path = os.path.join(temp_dir, f'seg_{i:05d}{ext}')
                    if os.path.exists(seg_path):
                        f.write(f"file '{os.path.abspath(seg_path).replace(chr(92), '/')}'" + chr(10))
                        break
        update_task_progress(task_id, status='merging', progress=95, speed='')
        save_tasks()
        merge_cmd = [FFMPEG_PATH, '-f', 'concat', '-safe', '0',
                     '-i', concat_file, '-c', 'copy', '-y', output_path]
        merge_proc = subprocess.run(merge_cmd, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=TIMEOUT_MERGE)
        shutil.rmtree(temp_dir, ignore_errors=True)
        if merge_proc.returncode == 0:
            final_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
            fail_count = sum(1 for s in seg_status if s == 2)
            update_task_progress(task_id, status='completed', progress=100, speed='',
                total_bytes=final_size, file_size=format_size(final_size),
                error=f'({fail_count}个分片最终失败)' if fail_count else '')
        else:
            err = merge_proc.stderr[-200:] if merge_proc.stderr else 'merge failed'
            update_task_progress(task_id, status='failed', error=f'重试合并失败: {err[:100]}')
    except Exception as e:
        log.warning(f"[retry] {task_id} error: {e}")
        update_task_progress(task_id, status='failed', error=str(e)[:100])
        shutil.rmtree(temp_dir, ignore_errors=True)
    save_tasks()
    scheduler_notify_completed(task_id)


@app.route('/api/download/force/<task_id>', methods=['POST'])
def api_download_force(task_id):
    with download_lock:
        if task_id not in download_tasks:
            return jsonify({'code': 404, 'message': '任务不存在'})
        task = download_tasks[task_id]
        if task['status'] in ('completed', 'merging'):
            return jsonify({'code': 400, 'message': '任务已结束或正在合并'})
    save_dir = task['save_dir']
    temp_dir = os.path.join(save_dir, f'.tmp_{task_id}')
    output_path = task.get('output_file', '')
    if not output_path:
        return jsonify({'code': 400, 'message': '无输出路径'})
    if not os.path.exists(temp_dir):
        return jsonify({'code': 400, 'message': '没有可用的分段目录'})
    seg_files = [f for f in os.listdir(temp_dir) if f.endswith('.ts') or f.endswith('.m4s')]
    if not seg_files:
        return jsonify({'code': 400, 'message': '没有可用的分段'})
    update_task_progress(task_id, status='merging', progress=95)
    save_tasks()
    thread = threading.Thread(target=_do_force_merge, args=(task_id, temp_dir, output_path), daemon=True, name=f'force-{task_id}')
    thread.start()
    return jsonify({'code': 200, 'message': f'正在强制合并 {len(seg_files)} 个分段'})


def _do_force_merge(task_id, temp_dir, output_path):
    try:
        def sort_key(fn):
            try:
                return int(fn.split('_')[1].split('.')[0])
            except (IndexError, ValueError):
                return 0
        seg_files = sorted([f for f in os.listdir(temp_dir) if f.endswith('.ts') or f.endswith('.m4s')], key=sort_key)
        concat_file = os.path.join(temp_dir, 'concat.txt')
        with open(concat_file, 'w', encoding='utf-8') as f:
            for sf in seg_files:
                f.write(f"file '{os.path.join(temp_dir, sf).replace(chr(92), '/')}'" + chr(10))
        merge_cmd = [FFMPEG_PATH, '-f', 'concat', '-safe', '0', '-i', concat_file, '-c', 'copy', '-y', output_path]
        merge_proc = subprocess.run(merge_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=TIMEOUT_MERGE)
        shutil.rmtree(temp_dir, ignore_errors=True)
        if merge_proc.returncode == 0:
            final_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
            update_task_progress(task_id, status='completed', progress=100, speed='', total_bytes=final_size, file_size=format_size(final_size))
        else:
            update_task_progress(task_id, status='failed', error='强制合并失败', speed='')
    except Exception as e:
        log.warning(f"[force] {task_id} error: {e}")
        update_task_progress(task_id, status='failed', error=str(e)[:80])
    save_tasks()
    scheduler_notify_completed(task_id)


@app.route('/api/download/concurrent', methods=['GET', 'POST'])
def api_concurrent():
    global DEFAULT_MAX_CONCURRENT
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        try:
            val = int(data.get('limit', 3))
            if 1 <= val <= MAX_CONCURRENT_LIMIT:
                DEFAULT_MAX_CONCURRENT = val
                log.info(f"[config] concurrent limit set to {val}")
                return jsonify({'code': 200, 'limit': val})
            return jsonify({'code': 400, 'message': f'范围 1-{MAX_CONCURRENT_LIMIT}'})
        except (ValueError, TypeError):
            return jsonify({'code': 400, 'message': '无效的数值'})
    return jsonify({'code': 200, 'limit': DEFAULT_MAX_CONCURRENT})


@app.route('/api/download/by-status')
def api_download_by_status():
    status_filter = request.args.get('status', '')
    with download_lock:
        all_tasks = list(download_tasks.values())
        all_tasks.reverse()
    if status_filter:
        statuses = [s.strip() for s in status_filter.split(',') if s.strip()]
        filtered = [t for t in all_tasks if t['status'] in statuses]
    else:
        filtered = all_tasks
    return jsonify({'code': 200, 'data': filtered})


@app.route('/api/ffmpeg/status')
def api_ffmpeg_status():
    ok, msg = check_ffmpeg()
    return jsonify({'code': 200, 'available': ok, 'path': FFMPEG_PATH or '', 'message': msg})


@app.route('/downloads')
def downloads_page():
    return render_template('downloads.html')


def main():
    start_scheduler()
    ffmpeg_status = FFMPEG_PATH or 'NOT FOUND'
    print("=" * 50)
    print("  m3u8 Search Tool v2 (with scheduler)")
    print(f"  target: {TARGET_BASE_URL}")
    print(f"  local:  http://127.0.0.1:5000")
    print(f"  ffmpeg: {ffmpeg_status}")
    print("=" * 50)
    app.run(debug=False, host='0.0.0.0', port=5000)


if __name__ == '__main__':
    main()
