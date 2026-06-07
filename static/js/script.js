/**
 * m3u8 搜索工具 - 前端 JS
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

    console.log('[晓晓] JS loaded, elements:', !!searchBtn, !!searchInput);

    // ===== 状态 =====
    let curKeyword = '', curPage = 1, totalPages = 1, totalItems = 0;

    // ===== 工具 =====
    function htmlEsc(s) { if (!s) return ''; var d = document.createElement('div'); d.appendChild(document.createTextNode(s)); return d.innerHTML; }

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
            return;
        }

        if (resultStats) {
            resultStats.innerHTML = '搜索 "<b>' + htmlEsc(curKeyword) + '</b>" | 第 ' + curPage + '/' + totalPages + ' 页 | 共 ' + totalItems + ' 条';
        }
        if (resultBar) resultBar.style.display = 'flex';
        if (selectAllCb) selectAllCb.checked = false;
        // 目录名不清空，保留上一次输入（除非用户手动改了）

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
                mhtml += '<div class="m3u8-item" data-title="' + safeT + '" data-url="' + url + '">'
                    + '<label class="select-item">'
                    + '<input type="checkbox" class="m3u8-checkbox" data-title="' + safeT + '" data-url="' + url + '">'
                    + '<span class="m3u8-url">' + url + '</span>'
                    + '</label>'
                    + '<button class="copy-btn" onclick="window.copyOne(this)">📋 复制</button>'
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

    // 图片加载失败时显示重试按钮
    window.imgErr = function(img, originalSrc) {
        if (!img || img.dataset.retried) return;
        img.dataset.retried = '1';
        // 用外层 wrap 替换为重试界面
        var wrap = img.parentNode;
        if (!wrap) return;
        wrap.innerHTML = '<div class="card-cover card-cover-fail" onclick="retryCover(this,\'' + originalSrc + '\')">'
                       + '<span>📸</span><span style="font-size:11px;color:#999;margin-top:2px;">加载失败，点击重试</span>'
                       + '</div>';
    };
    // 重试：重新创建 img 标签
    window.retryCover = function(el, src) {
        el.outerHTML = '<img class="card-cover" src="' + src + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">';
    };

    // 图片点击放大（用事件委托）
    if (resultList) resultList.addEventListener('click', function(e) {
        var cover = e.target.closest('.card-cover');
        if (cover && cover.tagName === 'IMG' && cover.src) openPreview(cover.src);
    });

    // ===== 复制 =====
    function getDir() { return dirInput ? dirInput.value.trim() : ''; }

    function makeLine(title, url) {
        var st = (title || '').slice(0, 10);
        var d = getDir();
        // Excel 格式：用制表符 \t 分隔，粘贴到 Excel 自动分列
        return d ? (url + '\t' + st + '\t' + d) : (url + '\t' + st);
    }

    // 复制单个（挂到 window 供 onclick 调用）
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

    // ===== 启动 =====
    if (searchInput) searchInput.focus();
    console.log('[晓晓] m3u8 搜索工具已启动');
})();
