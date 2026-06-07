# ============================================
# m3u8 搜索工具 - Python 后端
# 功能：接收前端搜索请求 -> 爬取目标站点 -> 解析 m3u8 地址 -> 返回 JSON
# 更新：前端分页，后端按页爬取
# 运行方式：python app.py
# 访问地址：http://192.168.70.172:5000（或 http://127.0.0.1:5000）
# ============================================

# === 导入模块 ===
import os
# 强制 Python 输出使用 UTF-8（解决 Windows GBK 终端中文崩溃）
from flask import Flask, request, jsonify, render_template
import requests
from bs4 import BeautifulSoup
import re
import urllib.parse

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
# 入口
# ============================================
if __name__ == '__main__':
    print("=" * 50)
    print("  m3u8 Search Tool (frontend pagination)")
    print(f"  target: {TARGET_BASE_URL}")
    print(f"  local:  http://127.0.0.1:5000")
    print(f"  lan:    http://192.168.70.172:5000")
    print("=" * 50)
    app.run(debug=True, host='0.0.0.0', port=5000)
