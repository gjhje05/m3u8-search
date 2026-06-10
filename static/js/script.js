/**
 * m3u8 搜索工具 - 前端 JS（含下载功能）
 */
(function() {
    'use strict';

    // ===== DOM 引用 =====
    const $ = id => document.getElementById(id);
    const searchInput   = $('search-input');
    const searchBtn     = $('search-btn');
    const resultList    = $('result-list');
    const loadingEl     = $('loading');
    const errorMsg      = $('error-msg');
    const resultBar     = $('result-bar');
    const resultStats   = $('result-stats');
    const copyAllBtn    = $('copy-all-btn');
    const selectAllCb   = $('select-all-checkbox');
    const dirInput      = $('dir-input');
    const pagination    = $('pagination');
    const pageInfo      = $('page-info');
    const prevBtn       = $('prev-page-btn');
    const nextBtn       = $('next-page-btn');
    const copyTa        = $('copy-textarea');
    const imgPrev       = $('img-preview');
    const imgPrevSrc    = $('img-preview-src');
    const imgPrevClose  = $('img-preview-close');
    const emptyState    = $('empty-state');

    // 下载相关 DOM
    const dlPanel       = $('dl-panel');
    const dlList        = $('dl-list');
    const dlEmptyMsg    = $('dl-empty-msg');
    const dlClearBtn    = $('dl-clear-btn');
    const dlToggleBtn   = $('dl-toggle-btn');
    const dlBatchBtn    = $('dl-batch-btn');
    const dlSettings    = $('dl-settings');
    const dlStartEp     = $('dl-start-ep');

    console.log('[晓晓] JS loaded with download support');

    // ===== 状态 =====
    let curKeyword = '', curPage = 1, totalPages = 1, totalItems = 0;
    let dlRefreshTimer = null;
    let dlPanelCollapsed = false;

    // ===== 工具 =====
    function htmlEsc(s) { if (!s) return ''; var d = document.createElement('div'); d.appendChild(document.createTextNode(s)); return d.innerHTML; }

    // ===== 获取默认下载目录（通过 API） =====
    const DFLT_SAVE_DIR = '';

    // 从 localStorage 恢复用户设置
    function loadDlSettings() {
        try {
            var saved = localStorage.getItem('m3u8_dl_settings');
            if (saved) {
                var s = JSON.parse(saved);
                if (s.startEp && dlStartEp) dlStartEp.value = s.startEp;
            }
        } catch(e) {}
    }
    function saveDlSettings() {
        try {
            localStorage.setItem('m3u8_dl_settings', JSON.stringify({
                startEp: dlStartEp ? dlStartEp.value : '1'
            }));
        } catch(e) {}
    }

    // 获取当前下载目录（从下载页共享的 localStorage 读取）
    function getSaveDir() {
        try {
            var saved = localStorage.getItem('m3u8_dl_save_dir');
            if (saved) return saved.trim();
        } catch(e) {}
        return '';
    }

    // 获取线程数（从下载页共享）
    function getThreadCount() {
        try {
            var saved = localStorage.getItem('m3u8_dl_threads');
            if (saved) {
                var v = parseInt(saved);
                if (v > 0 && v <= 128) return v;
            }
        } catch(e) {}
        return 36;
    }

    // 获取起始集数
    function getStartEp() {
        var v = parseInt(dlStartEp ? dlStartEp.value : '1');
        return isNaN(v) || v < 0 ? 0 : v;
    }

    function showDlSettings(v) {
        if (dlSettings) dlSettings.style.display = v ? 'flex' : 'none';
        if (dlBatchBtn) dlBatchBtn.style.display = v ? 'inline-block' : 'none';
    }

    // ===== UI 控制 =====
    function showLoading(b) { if (loadingEl) loadingEl.style.display = b ? 'block' : 'none'; }
    function showErr(msg) {
        if (!errorMsg) return;
        errorMsg.textContent = msg;
        errorMsg.style.display = 'block';
    }
    function hideErr() { if (errorMsg) { errorMsg.style.display = 'none'; errorMsg.textContent = ''; } }
    function clearAll() {
        if (resultList) resultList.innerHTML = '';
        if (resultBar) resultBar.style.display = 'none';
        if (pagination) pagination.style.display = 'none';
        hideErr();
        if (emptyState) emptyState.style.display = 'none';
        showDlSettings(false);
    }

    // ===== 下载 API 调用 =====
    function getSubDir() {
        return dirInput ? dirInput.value.trim() : '';
    }

    async function apiDownloadSingle(url, title) {
        var saveDir = getSaveDir();
        var subDir = getSubDir();
        var tc = getThreadCount();
        try {
            // 10 秒超时
            var controller = new AbortController();
            var timeout = setTimeout(function() { controller.abort(); }, 10000);
            var resp = await fetch('/api/download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    url: url,
                    title: title,
                    save_dir: saveDir,
                    episode: 0,
                    sub_dir: subDir,
                    thread_count: tc
                }),
                signal: controller.signal
            });
            clearTimeout(timeout);
            return await resp.json();
        } catch(e) {
            return { code: 500, message: '请求失败: ' + e.message };
        }
    }

    async function apiDownloadBatch(items) {
        var saveDir = getSaveDir();
        var startEp = getStartEp();
        var subDir = getSubDir();
        var tc = getThreadCount();
        try {
            var controller = new AbortController();
            var timeout = setTimeout(function() { controller.abort(); }, 10000);
            var resp = await fetch('/api/download/batch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    items: items,
                    save_dir: saveDir,
                    start_episode: startEp,
                    sub_dir: subDir,
                    thread_count: tc
                }),
                signal: controller.signal
            });
            clearTimeout(timeout);
            return await resp.json();
        } catch(e) {
            return { code: 500, message: '请求失败: ' + e.message };
        }
    }

    async function apiDownloadList() {
        try {
            var resp = await fetch('/api/download/list');
            return await resp.json();
        } catch(e) {
            return { code: 500, data: [] };
        }
    }

    async function apiDownloadCancel(taskId) {
        try {
            var resp = await fetch('/api/download/cancel/' + taskId, { method: 'POST' });
            return await resp.json();
        } catch(e) {}
    }

    async function apiDownloadClear() {
        try {
            var resp = await fetch('/api/download/clear', { method: 'POST' });
            return await resp.json();
        } catch(e) {}
    }

    // ===== 下载管理器面板 =====
    function renderDlPanel(tasks) {
        if (!dlList || !dlEmptyMsg || !dlPanel) return;

        if (!tasks || !tasks.length) {
            dlList.innerHTML = '';
            dlEmptyMsg.style.display = 'block';
            return;
        }

        dlEmptyMsg.style.display = 'none';

        var html = '';
        for (var i = 0; i < tasks.length; i++) {
            var t = tasks[i];
            html += buildDlTask(t);
        }
        dlList.innerHTML = html;
    }

    function buildDlTask(t) {
        var statusText = {
            'queued': '排队中',
            'downloading': '下载中',
            'completed': '已完成',
            'failed': '失败',
            'cancelled': '已取消'
        }[t.status] || t.status;

        var pct = t.progress || 0;
        var pctClass = 'dl-pg-' + (t.status || 'queued');
        var stClass = 'dl-st-' + (t.status || 'queued');

        var title = htmlEsc(t.title || '未知');
        var epBadge = t.episode > 0 ? '<span class="dl-ep-badge">E' + pad2(t.episode) + '</span>' : '';
        var outputInfo = t.output_file ? '<div class="dl-task-output" title="' + htmlEsc(t.output_file) + '">📁 ' + htmlEsc(t.output_file) + '</div>' : '';
        var errorInfo = t.error ? '<div class="dl-task-output" style="color:#e74c3c;">⚠️ ' + htmlEsc(t.error) + '</div>' : '';
        var cancelBtn = (t.status === 'queued' || t.status === 'downloading')
            ? '<button class="dl-task-cancel" onclick="window.__dlCancel(\'' + t.id + '\')">✕ 取消</button>'
            : '';

        return '<div class="dl-task" data-task-id="' + t.id + '">'
            + '<div class="dl-task-info">'
            + '<div class="dl-task-title">' + title + epBadge + '</div>'
            + '<div class="dl-task-meta">'
            + '<div class="dl-progress-wrap">'
            + '<div class="dl-progress-bar">'
            + '<div class="dl-progress-fill ' + pctClass + '" style="width:' + pct + '%;"></div>'
            + '</div></div>'
            + '<span class="dl-progress-text">' + pct + '%</span>'
            + '<span class="dl-task-status ' + stClass + '">' + statusText + '</span>'
            + '</div>'
            + outputInfo
            + errorInfo
            + '</div>'
            + cancelBtn
            + '</div>';
    }

    function pad2(n) { return n < 10 ? '0' + n : '' + n; }

    // 取消下载（挂全局供内联 onclick 调用）
    window.__dlCancel = function(taskId) {
        apiDownloadCancel(taskId).then(function() { refreshDlList(); });
    };

    async function refreshDlList() {
        var result = await apiDownloadList();
        var tasks = result.data || [];
        renderDlPanel(tasks);
        // 如果有活跃任务，继续刷新
        var hasActive = tasks.some(function(t) { return t.status === 'queued' || t.status === 'downloading'; });
        if (hasActive && dlPanel && dlPanel.style.display !== 'none') {
            scheduleDlRefresh();
        } else {
            stopDlRefresh();
        }
    }

    function scheduleDlRefresh() {
        stopDlRefresh();
        dlRefreshTimer = setTimeout(refreshDlList, 2000);
    }

    function stopDlRefresh() {
        if (dlRefreshTimer) {
            clearTimeout(dlRefreshTimer);
            dlRefreshTimer = null;
        }
    }

    function startDlRefresh() {
        showDlPanel(true);
        refreshDlList();
    }

    function showDlPanel(show) {
        if (!dlPanel) return;
        dlPanel.style.display = show ? 'block' : 'none';
        if (show) {
            refreshDlList();
        } else {
            stopDlRefresh();
        }
    }

    function toggleDlPanel() {
        if (!dlPanel) return;
        dlPanelCollapsed = !dlPanelCollapsed;
        if (dlList) dlList.style.display = dlPanelCollapsed ? 'none' : 'block';
        if (dlEmptyMsg) dlEmptyMsg.style.display = (dlPanelCollapsed && dlList && dlList.innerHTML === '') ? 'none' : (dlPanelCollapsed ? 'none' : 'block');
        if (dlToggleBtn) dlToggleBtn.textContent = dlPanelCollapsed ? '+' : '−';
    }

    // ===== 批量下载 =====
    async function batchDownload() {
        // 收集所有勾选的 m3u8
        var cbs = document.querySelectorAll('.m3u8-checkbox:checked');
        if (!cbs.length) { showErr('请先勾选要下载的项目'); return; }

        var items = [];
        for (var i = 0; i < cbs.length; i++) {
            var url = cbs[i].getAttribute('data-url');
            var title = cbs[i].getAttribute('data-title') || '未知视频';
            if (url) {
                items.push({ url: url, title: title });
            }
        }

        if (!items.length) { showErr('无有效下载地址'); return; }

        if (dlBatchBtn) {
            dlBatchBtn.disabled = true;
            dlBatchBtn.textContent = '⏳ 提交中...';
        }

        var result = await apiDownloadBatch(items);
        if (result && result.code === 200) {
            if (dlBatchBtn) {
                dlBatchBtn.textContent = '✅ 已提交 ' + result.count + ' 个';
                setTimeout(function() {
                    dlBatchBtn.disabled = false;
                    dlBatchBtn.textContent = '📥 批量下载';
                }, 2000);
            }
            startDlRefresh();
        } else {
            showErr('批量下载失败: ' + (result ? result.message : '未知错误'));
            if (dlBatchBtn) {
                dlBatchBtn.disabled = false;
                dlBatchBtn.textContent = '📥 批量下载';
            }
        }
    }

    // ===== 搜索 =====
    async function doSearch(page) {
        if (page === undefined) page = 1;
        var kw = searchInput ? searchInput.value.trim() : '';
        if (!kw) { showErr('请输入关键词'); if (searchInput) searchInput.focus(); return; }

        clearAll();
        showLoading(true);
        if (searchBtn) { searchBtn.disabled = true; searchBtn.textContent = '⏳ ...'; }

        try {
            var resp = await fetch('/api/search?wd=' + encodeURIComponent(kw) + '&page=' + page);
            var result = await resp.json();
            showLoading(false);

            if (result.code === 200) {
                curPage = page;
                curKeyword = kw;
                if (result.page_info) {
                    totalPages = result.page_info.total || 1;
                    totalItems = result.page_info.total_items || 0;
                    curPage = result.page_info.current || page;
                }
                renderAll(result.data || []);
                // 搜索成功后保存状态（去下载页返回时恢复）
                saveSearchState();
            } else {
                showErr(result.message || '搜索失败');
            }
        } catch (e) {
            showLoading(false);
            showErr('无法连接后端，请运行 python app.py');
            console.error('[晓晓] fetch error:', e);
        } finally {
            if (searchBtn) { searchBtn.disabled = false; searchBtn.textContent = '🚀 搜索'; }
        }
    }

    // ===== 渲染 =====
    function renderAll(videos) {
        if (!videos || !videos.length) {
            if (emptyState) {
                emptyState.querySelector('.empty-text').textContent = '未找到';
                emptyState.style.display = 'block';
            }
            if (resultBar) resultBar.style.display = 'none';
            if (pagination) pagination.style.display = 'none';
            showDlSettings(false);
            return;
        }

        if (resultStats) {
            resultStats.innerHTML = '搜索 "<b>' + htmlEsc(curKeyword) + '</b>" | 第 ' + curPage + '/' + totalPages + ' 页 | 共 ' + totalItems + ' 条';
        }
        if (resultBar) resultBar.style.display = 'flex';
        if (selectAllCb) selectAllCb.checked = false;
        // 显示下载设置区域
        showDlSettings(true);

        var html = '';
        for (var i = 0; i < videos.length; i++) {
            html += buildCard(videos[i], i);
        }
        if (resultList) resultList.innerHTML = html;

        updatePage();
    }

    function buildCard(v, idx) {
        var title = v.title || '未知';
        var safeT = htmlEsc(title);

        var cover = v.cover_img
            ? '<div class="card-cover-wrap">'
              + '<img class="card-cover" src="' + htmlEsc(v.cover_img) + '" alt="' + safeT + '" loading="lazy"'
              + ' onerror="window.imgErr(this,\'' + htmlEsc(v.cover_img) + '\')">'
              + '</div>'
            : '<div class="card-cover" style="background:#eee;display:flex;align-items:center;justify-content:center;font-size:2rem;">📹</div>';

        var link = v.detail_url
            ? '<a class="card-detail-link" href="' + htmlEsc(v.detail_url) + '" target="_blank" rel="noopener">详情 →</a>'
            : '';

        var mhtml = '';
        if (v.m3u8_urls && v.m3u8_urls.length) {
            mhtml = '<div class="m3u8-section-title">📡 m3u8（共' + v.m3u8_urls.length + '个）：</div>';
            for (var j = 0; j < v.m3u8_urls.length; j++) {
                var url = htmlEsc(v.m3u8_urls[j]);
                var epIndex = j + 1;
                mhtml += '<div class="m3u8-item" data-title="' + safeT + '" data-url="' + url + '">'
                    + '<label class="select-item">'
                    + '<input type="checkbox" class="m3u8-checkbox" data-title="' + safeT + '" data-url="' + url + '">'
                    + '<span class="m3u8-url">' + url + '</span>'
                    + '</label>'
                    + '<button class="copy-btn" onclick="window.copyOne(this)">📋 复制</button>'
                    + '<button class="dl-single-btn">⬇️ 下载</button>'
                    + '</div>';
            }
        } else {
            mhtml = '<div class="no-m3u8">⚠️ 无 m3u8 地址</div>';
        }

        return '<div class="video-card">'
            + '<div class="card-header">' + cover
            + '<div class="card-info">'
            + '<div class="card-title" title="' + safeT + '">' + safeT + '</div>'
            + link
            + '</div></div>'
            + mhtml
            + '</div>';
    }

    // ===== 分页 =====
    function updatePage() {
        if (!pagination) return;
        if (totalPages <= 1) { pagination.style.display = 'none'; return; }
        pagination.style.display = 'flex';
        if (pageInfo) pageInfo.textContent = '第 ' + curPage + ' / ' + totalPages + ' 页';
        if (prevBtn) prevBtn.disabled = (curPage <= 1);
        if (nextBtn) nextBtn.disabled = (curPage >= totalPages);
    }

    if (prevBtn) prevBtn.onclick = function() { if (curPage > 1) { if (searchInput) searchInput.value = curKeyword; doSearch(curPage - 1); } };
    if (nextBtn) nextBtn.onclick = function() { if (curPage < totalPages) { if (searchInput) searchInput.value = curKeyword; doSearch(curPage + 1); } };

    // ===== 全选 =====
    if (selectAllCb) selectAllCb.onchange = function() {
        var cbs = document.querySelectorAll('.m3u8-checkbox');
        for (var i = 0; i < cbs.length; i++) cbs[i].checked = this.checked;
    };

    // ===== 图片预览 =====
    function openPreview(src) {
        if (imgPrevSrc) imgPrevSrc.src = src;
        if (imgPrev) imgPrev.style.display = 'flex';
    }
    function closePreview() {
        if (imgPrev) imgPrev.style.display = 'none';
        if (imgPrevSrc) imgPrevSrc.src = '';
    }
    if (imgPrevClose) imgPrevClose.onclick = closePreview;
    if (imgPrev) imgPrev.onclick = function(e) { if (e.target === this) closePreview(); };
    document.addEventListener('keydown', function(e) { if (e.key === 'Escape' && imgPrev && imgPrev.style.display === 'flex') closePreview(); });

    window.imgErr = function(img, originalSrc) {
        if (!img || img.dataset.retried) return;
        img.dataset.retried = '1';
        var wrap = img.parentNode;
        if (!wrap) return;
        wrap.innerHTML = '<div class="card-cover card-cover-fail" onclick="retryCover(this,\'' + originalSrc + '\')">'
                       + '<span>📸</span><span style="font-size:11px;color:#999;margin-top:2px;">加载失败，点击重试</span>'
                       + '</div>';
    };
    window.retryCover = function(el, src) {
        el.outerHTML = '<img class="card-cover" src="' + src + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">';
    };

    if (resultList) resultList.addEventListener('click', function(e) {
        var cover = e.target.closest('.card-cover');
        if (cover && cover.tagName === 'IMG' && cover.src) openPreview(cover.src);
    });

    // ===== 复制 =====
    function getDir() { return dirInput ? dirInput.value.trim() : ''; }

    function makeLine(title, url) {
        var st = (title || '').slice(0, 10);
        var d = getDir();
        return d ? (url + '\t' + st + '\t' + d) : (url + '\t' + st);
    }

    window.copyOne = function(btn) {
        var item = btn.closest('.m3u8-item');
        if (!item) return;
        var cb = item.querySelector('.m3u8-checkbox');
        if (cb) cb.checked = true;
        var t = item.getAttribute('data-title') || '';
        var u = item.getAttribute('data-url') || '';
        var text = makeLine(t, u);
        copyClip(text);
        btn.textContent = '✅';
        btn.classList.add('copied');
        setTimeout(function() { btn.textContent = '📋 复制'; btn.classList.remove('copied'); }, 1200);
    };

    function copySelected() {
        var cbs = document.querySelectorAll('.m3u8-checkbox:checked');
        if (!cbs.length) { showErr('请先勾选'); return; }
        var lines = [];
        for (var i = 0; i < cbs.length; i++) {
            var t = cbs[i].getAttribute('data-title') || '未知';
            var u = cbs[i].getAttribute('data-url') || '';
            if (u) lines.push(makeLine(t, u));
        }
        if (!lines.length) { showErr('无内容'); return; }
        copyClip(lines.join('\n'));

        var info = getDir() ? ' +目录' : '';
        if (copyAllBtn) {
            copyAllBtn.textContent = '✅ 已复制 ' + lines.length + ' 条' + info;
            copyAllBtn.classList.add('copied');
            setTimeout(function() {
                copyAllBtn.textContent = '📋 复制选中';
                copyAllBtn.classList.remove('copied');
            }, 2000);
        }
    }

    function copyClip(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).catch(function() { fallbackCopy(text); });
        } else {
            fallbackCopy(text);
        }
    }
    function fallbackCopy(text) {
        if (!copyTa) return;
        copyTa.value = text;
        copyTa.style.left = '0';
        copyTa.select();
        try { document.execCommand('copy'); } catch(e) {}
        copyTa.style.left = '-9999px';
    }

    // ===== 事件绑定 =====
    if (searchBtn) searchBtn.onclick = function() { doSearch(1); };
    if (searchInput) searchInput.onkeydown = function(e) { if (e.key === 'Enter') { e.preventDefault(); doSearch(1); } };
    if (copyAllBtn) copyAllBtn.onclick = copySelected;

    // 单个下载（事件委托）
    if (resultList) resultList.addEventListener('click', function(e) {
        var btn = e.target.closest('.dl-single-btn');
        if (!btn) return;
        var item = btn.closest('.m3u8-item');
        if (!item) return;
        var url = item.getAttribute('data-url') || '';
        var title = item.getAttribute('data-title') || '未知';
        if (!url) return;
        
        btn.disabled = true;
        btn.textContent = '⏳';
        btn.classList.add('dl-starting');

        apiDownloadSingle(url, title).then(function(result) {
            if (result && result.code === 200) {
                btn.textContent = '✅';
                setTimeout(function() {
                    btn.disabled = false;
                    btn.textContent = '⬇️ 下载';
                    btn.classList.remove('dl-starting');
                }, 1500);
                startDlRefresh();
            } else {
                btn.textContent = '❌';
                showErr('下载失败: ' + (result ? result.message : '未知错误'));
                setTimeout(function() {
                    btn.disabled = false;
                    btn.textContent = '⬇️ 下载';
                    btn.classList.remove('dl-starting');
                }, 2000);
            }
        });
    });

    // 批量下载事件
    if (dlBatchBtn) dlBatchBtn.onclick = batchDownload;
    if (dlClearBtn) dlClearBtn.onclick = function() {
        apiDownloadClear().then(function() { refreshDlList(); });
    };
    if (dlToggleBtn) dlToggleBtn.onclick = toggleDlPanel;

    // 保存起始集数到 localStorage
    if (dlStartEp) dlStartEp.addEventListener('change', saveDlSettings);

    // 加载设置
    loadDlSettings();

    // ===== 搜索状态持久化（从下载页返回时不丢失） =====
    function saveSearchState() {
        try {
            if (curKeyword && curKeyword.length > 0) {
                var data = JSON.stringify({ keyword: curKeyword, page: curPage || 1 });
                localStorage.setItem('m3u8_search_state', data);
            }
        } catch(e) {}
    }

    function restoreSearchState() {
        try {
            var saved = localStorage.getItem('m3u8_search_state');
            if (!saved) return;
            var s = JSON.parse(saved);
            if (s.keyword && s.keyword.length > 0 && searchInput) {
                searchInput.value = s.keyword;
                var page = (s.page && s.page > 0) ? s.page : 1;
                // 延迟确保 DOM 就绪
                setTimeout(function() { doSearch(page); }, 100);
            }
        } catch(e) {}
    }

    // ===== 启动 =====
    if (searchInput) searchInput.focus();
    restoreSearchState();
    console.log('[晓晓] m3u8 搜索工具已启动（带下载支持）');
})();
