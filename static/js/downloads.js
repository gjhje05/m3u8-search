/**
 * 下载管理页面 - 独立下载管理页 JS
 * 功能：四标签页（下载中/等待中/已完成/失败/取消）、自动刷新、数据持久化
 */
(function() {
    'use strict';

    const $ = id => document.getElementById(id);

    // ===== DOM =====
    const tabBtns    = document.querySelectorAll('.dl-tab');
    const taskList   = $('dl-task-list');
    const loadingEl  = $('dl-loading');
    const emptyEl    = $('dl-empty');
    const emptyText  = $('dl-empty-text');
    const statsText  = $('dl-stats-text');
    const clearBtn   = $('dl-clear-all-btn');
    const refreshBtn = $('dl-refresh-btn');
    const dlSaveDirInput = $('dl-save-dir-input');
    const dlResetDirBtn  = $('dl-reset-dir');
    const dlThreadInput  = $('dl-thread-input');
    const dlConcurrentInput = $('dl-concurrent-input');

    // ===== 状态 =====
    let currentTab = 'downloading';
    let refreshTimer = null;

    // ===== 工具 =====
    function htmlEsc(s) { if (!s) return ''; var d = document.createElement('div'); d.appendChild(document.createTextNode(s)); return d.innerHTML; }

    function formatTime(iso) {
        if (!iso) return '';
        try {
            var d = new Date(iso);
            var pad = n => n < 10 ? '0' + n : '' + n;
            return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
        } catch(e) { return iso; }
    }

    function pad2(n) { return n < 10 ? '0' + n : '' + n; }

    // ===== 标签页 → 状态过滤映射 =====
    var TAB_STATUS_MAP = {
        downloading: 'downloading,queued,merging',
        waiting: 'waiting',
        completed: 'completed',
        failed: 'failed,cancelled'
    };

    var TAB_NAMES = {
        downloading: '下载中',
        waiting: '等待中',
        completed: '已完成',
        failed: '失败/取消'
    };

    // ===== 加载任务 =====
    var _initialLoad = true;

    async function loadTasks() {
        if (_initialLoad) showLoading(true);

        try {
            var statusFilter = TAB_STATUS_MAP[currentTab] || currentTab;
            var resp = await fetch('/api/download/by-status?status=' + encodeURIComponent(statusFilter));
            var result = await resp.json();
            _initialLoad = false;
            showLoading(false);

            if (result.code === 200) {
                renderTasks(result.data || []);
                updateStats();
                // 检查是否需要继续刷新
                autoRefreshIfNeeded(result.data || []);
            } else {
                renderTasks([]);
            }
        } catch(e) {
            _initialLoad = false;
            showLoading(false);
            renderTasks([]);
            console.error('[DlPage] fetch error:', e);
        }
    }

    // ===== 格式化文件大小 =====
    function formatSize(bytes) {
        if (!bytes || bytes <= 0) return '0B';
        if (bytes < 1024) return bytes + 'B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + 'KB';
        if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + 'MB';
        return (bytes / 1024 / 1024 / 1024).toFixed(2) + 'GB';
    }

    // ===== 更新统计 =====
    async function updateStats() {
        try {
            var resp = await fetch('/api/download/list');
            var result = await resp.json();
            var all = result.data || [];
            var d = all.filter(function(t) { return t.status === 'downloading' || t.status === 'queued'; }).length;
            var w = all.filter(function(t) { return t.status === 'waiting'; }).length;
            var c = all.filter(function(t) { return t.status === 'completed'; }).length;
            var f = all.filter(function(t) { return t.status === 'failed' || t.status === 'cancelled'; }).length;

            var text = '⬇️ ' + d + '  ⏳ ' + w + '  ✅ ' + c + '  ❌ ' + f + '  | 共 ' + all.length + ' 个';
            if (statsText) statsText.textContent = text;
        } catch(e) {}
    }

    // ===== 智能渲染（原地更新，不闪动） =====
    function renderTasks(tasks) {
        if (!tasks || !tasks.length) {
            taskList.innerHTML = '';
            emptyEl.style.display = 'block';
            emptyText.textContent = '暂无' + (TAB_NAMES[currentTab] || '') + '的任务';
            return;
        }

        emptyEl.style.display = 'none';

        var existingCards = taskList.querySelectorAll('.dl-task-card');
        var existingMap = {};
        existingCards.forEach(function(c) { existingMap[c.getAttribute('data-id')] = c; });

        var newHtml = '';
        var hasNewCards = false;

        for (var i = 0; i < tasks.length; i++) {
            var t = tasks[i];
            var existing = existingMap[t.id];
            if (existing) {
                // 原地更新进度和速度（不替换整个卡片，避免闪动）
                updateCardInPlace(existing, t);
                delete existingMap[t.id];
            } else {
                // 新卡片追加
                newHtml += buildTaskCard(t);
                hasNewCards = true;
            }
        }

        // 移除已经不存在的卡片
        for (var id in existingMap) {
            if (existingMap.hasOwnProperty(id)) {
                existingMap[id].remove();
            }
        }

        // 追加新卡片
        if (hasNewCards && newHtml) {
            taskList.insertAdjacentHTML('beforeend', newHtml);
        }
    }

    function updateCardInPlace(card, t) {
        var fill = card.querySelector('.dl-progress-fill');
        if (fill) {
            fill.style.width = (t.progress || 0) + '%';
            fill.className = 'dl-progress-fill dl-fill-' + (t.status || 'queued');
        }
        var pct = card.querySelector('.dl-progress-text');
        if (pct) pct.textContent = (t.progress || 0) + '%';
        var badge = card.querySelector('.dl-task-status-badge');
        if (badge) {
            var labels = { queued: '排队中', waiting: '等待中', downloading: '下载中', merging: '合并中', completed: '已完成', failed: '失败', cancelled: '已取消' };
            badge.textContent = labels[t.status] || t.status;
            badge.className = 'dl-task-status-badge dl-status-' + (t.status || 'queued');
        }
        // 更新速度/大小
        var infoGroup = card.querySelector('.dl-info-group');
        if (infoGroup) {
            var ih = '';
            if (t.speed) ih += '<span class="dl-speed-badge">⚡ ' + t.speed + '</span>';
            if (t.file_size) ih += '<span class="dl-size-badge">📦 ' + t.file_size + '</span>';
            infoGroup.innerHTML = ih;
        }
        // 更新错误
        var errEl = card.querySelector('.dl-meta-error');
        if (t.error) {
            var em = '⚠️ ' + htmlEsc(t.error);
            if (errEl) { errEl.textContent = em; }
            else {
                var m2 = card.querySelector('.dl-meta-left');
                if (m2) { var e = document.createElement('div'); e.className = 'dl-meta-error'; e.textContent = em; m2.appendChild(e); }
            }
        } else if (errEl) { errEl.remove(); }
        // 更新分段网格
        updateSegGrid(card, t);
    }

    function updateSegGrid(card, t) {
        var segContainer = card.querySelector('.dl-seg-grid');
        if (!segContainer) return;
        var ss = t.seg_status;
        if (!ss || !ss.length) { segContainer.style.display = 'none'; return; }
        segContainer.style.display = 'flex';
        // 只更新有变化的部分：统计 + 网格点亮数量
        var segStats = card.querySelector('.dl-seg-stats');
        if (segStats) {
            var pending = ss.filter(function(s) { return s === 0; }).length;
            var success = ss.filter(function(s) { return s === 1; }).length;
            var error = ss.filter(function(s) { return s === 2; }).length;
            segStats.textContent = '分片: ⬜' + pending + ' ✅' + success + ' ❌' + error;
        }
        // 更新网格颜色（只更新已有格子，不重建）
        var items = segContainer.querySelectorAll('.seg-item');
        for (var i = 0; i < items.length && i < ss.length; i++) {
            var cls = 'seg-item';
            if (ss[i] === 1) cls += ' seg-ok';
            else if (ss[i] === 2) cls += ' seg-err';
            items[i].className = cls;
        }
    }

    function buildTaskCard(t) {
        var statusLabels = {
            queued: '排队中',
            waiting: '等待中',
            downloading: '下载中',
            merging: '合并中',
            completed: '已完成',
            failed: '失败',
            cancelled: '已取消'
        };
        var statusLabel = statusLabels[t.status] || t.status;
        var pct = t.progress || 0;
        var fillClass = 'dl-fill-' + (t.status || 'queued');
        var badgeClass = 'dl-status-' + (t.status || 'queued');

        var title = htmlEsc(t.title || '未知视频');
        var epBadge = t.episode > 0 ? '<span class="dl-task-ep">E' + pad2(t.episode) + '</span>' : '';
        var fileInfo = t.output_file
            ? '<div class="dl-meta-file" title="' + htmlEsc(t.output_file) + '">📁 ' + htmlEsc(t.output_file) + '</div>'
            : '';
        var timeInfo = '';
        if (t.status === 'completed' && t.started_at) {
            timeInfo = '<div class="dl-meta-time">完成于 ' + formatTime(t.started_at) + '</div>';
        } else if (t.started_at) {
            timeInfo = '<div class="dl-meta-time">开始于 ' + formatTime(t.started_at) + '</div>';
        }
        var errorInfo = t.error
            ? '<div class="dl-meta-error">⚠️ ' + htmlEsc(t.error) + '</div>'
            : '';

        // 操作按钮
        var actionsHtml = '';
        if (t.status === 'queued' || t.status === 'downloading') {
            actionsHtml += '<button class="dl-action-btn dl-act-cancel" onclick="window.dlCancel(\'' + t.id + '\')">✕ 取消</button>';
        }
        if (t.status === 'waiting') {
            // 等待任务：显示删除按钮
            actionsHtml += '<button class="dl-action-btn dl-act-remove" onclick="window.dlRemove(\'' + t.id + '\')">🗑️ 删除</button>';
        }
        if (t.status === 'completed' && t.output_file) {
            actionsHtml += '<button class="dl-action-btn dl-act-open" onclick="window.dlOpenFolder(\'' + htmlEsc(t.output_file.replace(/\\/g,'\\\\')) + '\')">📂 打开</button>';
        }
        if (t.status === 'failed' && t.seg_status && t.seg_status.some(function(s) { return s === 2; })) {
            actionsHtml += '<button class="dl-action-btn dl-act-retry" onclick="window.dlRetry(\'' + t.id + '\')">♻️ 重试失败</button>';
        }

        // 分段网格（参考 m3u8-downloader 的切片粒度和颜色）
        var segGrid = '';
        var ss = t.seg_status;
        if (ss && ss.length > 0) {
            var pending = ss.filter(function(s) { return s === 0; }).length;
            var success = ss.filter(function(s) { return s === 1; }).length;
            var error = ss.filter(function(s) { return s === 2; }).length;
            // 只显示前 500 个网格避免太卡，多的用文字
            var gridHtml = '';
            var maxShow = Math.min(ss.length, 500);
            for (var k = 0; k < maxShow; k++) {
                var cls = 'seg-item';
                if (ss[k] === 1) cls += ' seg-ok';
                else if (ss[k] === 2) cls += ' seg-err';
                gridHtml += '<span class="' + cls + '"></span>';
            }
            var overflow = ss.length > 500 ? '<span class="seg-more">...共' + ss.length + '片</span>' : '';
            segGrid = '<div class="dl-seg-grid">'
                + '<div class="dl-seg-stats">分片: ⬜' + pending + ' ✅' + success + ' ❌' + error + '</div>'
                + '<div class="dl-seg-grid-inner">' + gridHtml + overflow + '</div>'
                + '</div>';
        }

        // 额外操作按钮
        var extraActions = '';
        if (t.status === 'failed' && ss && ss.some(function(s) { return s === 2; })) {
            extraActions += '<button class="dl-action-btn dl-act-retry" onclick="window.dlRetry(\'' + t.id + '\')">♻️ 重试失败</button>';
        }
        if (t.status === 'downloading' && ss && ss.some(function(s) { return s === 1; })) {
            extraActions += '<button class="dl-action-btn dl-act-force" onclick="window.dlForce(\'' + t.id + '\')">📥 强制合并现有</button>';
        }

        return '<div class="dl-task-card" data-id="' + t.id + '">'
            + '<div class="dl-task-top">'
            + '<div class="dl-task-title">' + title + epBadge + '</div>'
            + '<span class="dl-task-status-badge ' + badgeClass + '">' + statusLabel + '</span>'
            + '</div>'
            + '<div class="dl-task-progress">'
            + '<div class="dl-progress-bar"><div class="dl-progress-fill ' + fillClass + '" style="width:' + pct + '%;"></div></div>'
            + '<span class="dl-progress-text">' + pct + '%</span>'
            + '</div>'
            + '<div class="dl-task-meta">'
            + '<div class="dl-meta-left">'
            + fileInfo
            + timeInfo
            + '<div class="dl-info-group"></div>'
            + errorInfo
            + '</div>'
            + '<div class="dl-task-actions">' + actionsHtml + extraActions + '</div>'
            + '</div>'
            + segGrid
            + '</div>';
    }

    // ===== 操作函数 =====
    window.dlCancel = async function(taskId) {
        try {
            await fetch('/api/download/cancel/' + taskId, { method: 'POST' });
            loadTasks();
        } catch(e) {}
    };

    window.dlRemove = async function(taskId) {
        try {
            await fetch('/api/download/remove/' + taskId, { method: 'POST' });
            loadTasks();
        } catch(e) {}
    };

    window.dlRetry = async function(taskId) {
        try {
            await fetch('/api/download/retry/' + taskId, { method: 'POST' });
            loadTasks();
        } catch(e) {}
    };

    window.dlForce = async function(taskId) {
        try {
            await fetch('/api/download/force/' + taskId, { method: 'POST' });
            loadTasks();
        } catch(e) {}
    };

    window.dlOpenFolder = function(filePath) {
        // 用 ShellExecute 打开文件所在文件夹
        try {
            var shell = new ActiveXObject('Shell.Application');
            shell.ShellExecute('explorer.exe', '/select,"' + filePath + '"', '', '', 1);
        } catch(e) {
            // 在普通浏览器中可能不工作，尝试更通用的方式
            var idx = filePath.lastIndexOf('\\');
            if (idx > 0) {
                var folder = filePath.substring(0, idx);
                window.open('file:///' + folder.replace(/\\/g, '/'));
            }
        }
    };

    // ===== 清空等待队列 =====
    window.dlClearQueue = async function() {
        try {
            await fetch('/api/download/clear-queue', { method: 'POST' });
            loadTasks();
            tabBtns.forEach(function(b) { b.classList.remove('active'); });
            // 切换到等待标签页
            var waitingTab = Array.from(tabBtns).find(function(b) { return b.getAttribute('data-tab') === 'waiting'; });
            if (waitingTab) {
                waitingTab.classList.add('active');
                currentTab = 'waiting';
            }
        } catch(e) {}
    };

    // ===== UI 控制 =====
    function showLoading(show) {
        if (loadingEl) loadingEl.style.display = show ? 'block' : 'none';
    }

    // ===== 智能自动刷新（有活跃任务才刷） =====
    function autoRefreshIfNeeded(tasks) {
        var hasActive = tasks.some(function(t) {
            return t.status === 'downloading' || t.status === 'queued' || t.status === 'waiting';
        });
        // 全局查所有任务是否有活跃的（可能在别的标签页）
        fetchActiveCheck(hasActive);
    }

    var _activeCheckPending = false;
    function fetchActiveCheck(localActive) {
        if (localActive) {
            ensureRefresh();
            return;
        }
        // 本地没有，查全局
        if (_activeCheckPending) return;
        _activeCheckPending = true;
        fetch('/api/download/list').then(function(r) { return r.json(); }).then(function(result) {
            _activeCheckPending = false;
            var all = result.data || [];
            var globalActive = all.some(function(t) {
                return t.status === 'downloading' || t.status === 'queued' || t.status === 'waiting';
            });
            if (globalActive) {
                ensureRefresh();
            } else {
                stopRefresh();
            }
        }).catch(function() { _activeCheckPending = false; stopRefresh(); });
    }

    function ensureRefresh() {
        if (!refreshTimer) {
            refreshTimer = setInterval(loadTasks, 3000);
        }
    }

    function stopRefresh() {
        if (refreshTimer) {
            clearInterval(refreshTimer);
            refreshTimer = null;
        }
    }

    // ===== 保存目录 =====
    function loadSaveDir() {
        try {
            var saved = localStorage.getItem('m3u8_dl_save_dir');
            if (saved && dlSaveDirInput) {
                dlSaveDirInput.value = saved;
            }
        } catch(e) {}
    }

    function saveDirVal() {
        if (!dlSaveDirInput) return '';
        var v = dlSaveDirInput.value.trim();
        try {
            localStorage.setItem('m3u8_dl_save_dir', v);
        } catch(e) {}
        return v;
    }

    function resetDirVal() {
        if (dlSaveDirInput) {
            dlSaveDirInput.value = '';
            localStorage.removeItem('m3u8_dl_save_dir');
        }
    }

    if (dlSaveDirInput) {
        dlSaveDirInput.addEventListener('change', saveDirVal);
        dlSaveDirInput.addEventListener('blur', saveDirVal);
    }
    if (dlResetDirBtn) dlResetDirBtn.addEventListener('click', resetDirVal);

    // ===== 线程数 =====
    function loadThreadCount() {
        try {
            var saved = localStorage.getItem('m3u8_dl_threads');
            if (saved && dlThreadInput) dlThreadInput.value = saved;
        } catch(e) {}
    }
    function saveThreadCount() {
        if (!dlThreadInput) return;
        try { localStorage.setItem('m3u8_dl_threads', dlThreadInput.value); } catch(e) {}
    }
    function getThreadCount() {
        var v = parseInt(dlThreadInput ? dlThreadInput.value : '36');
        return (v > 0 && v <= 128) ? v : 36;
    }
    if (dlThreadInput) {
        dlThreadInput.addEventListener('change', saveThreadCount);
        dlThreadInput.addEventListener('blur', saveThreadCount);
    }

    // ===== 并发任务数 =====
    function loadConcurrentLimit() {
        fetch('/api/download/concurrent').then(function(r){return r.json();}).then(function(d){
            if(d.code===200 && dlConcurrentInput) dlConcurrentInput.value=d.limit;
        }).catch(function(){});
    }
    function saveConcurrentLimit() {
        if(!dlConcurrentInput) return;
        var v=parseInt(dlConcurrentInput.value)||3;
        if(v<1)v=1; if(v>20)v=20;
        dlConcurrentInput.value=v;
        fetch('/api/download/concurrent',{
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({limit:v})
        }).catch(function(){});
    }
    if(dlConcurrentInput){
        dlConcurrentInput.addEventListener('change',saveConcurrentLimit);
        dlConcurrentInput.addEventListener('blur',saveConcurrentLimit);
    }

    // ===== 清空等待 =====
    var clearQueueBtn = $('dl-clear-queue-btn');
    if (clearQueueBtn) clearQueueBtn.addEventListener('click', function() {
        if (!confirm('确定清空所有等待和排队中的任务吗？')) return;
        window.dlClearQueue();
    });

    // ===== 清除已完成/已失败 =====
    if (clearBtn) clearBtn.addEventListener('click', async function() {
        try {
            await fetch('/api/download/clear', { method: 'POST' });
            loadTasks();
        } catch(e) {}
    });

    // ===== 手动刷新 =====
    if (refreshBtn) refreshBtn.addEventListener('click', function() {
        stopRefresh();
        loadTasks();
    });

    // 标签页切换时重新加载
    tabBtns.forEach(function(btn) {
        btn.addEventListener('click', function() {
            tabBtns.forEach(function(b) { b.classList.remove('active'); });
            this.classList.add('active');
            currentTab = this.getAttribute('data-tab') || 'downloading';
            stopRefresh();
            loadTasks();
        });
    });

    // 页面隐藏时停止刷新
    document.addEventListener('visibilitychange', function() {
        if (document.hidden) {
            stopRefresh();
        }
    });

    // ===== 启动 =====
    function init() {
        loadSaveDir();
        loadThreadCount();
        loadConcurrentLimit();
        loadTasks();
    }

    init();

})();
