// ============================================================
// SCRIPT.JS — Secure Frontend Logic (Full Update)
// ============================================================

const tg = window.Telegram.WebApp;
tg.expand();
tg.enableClosingConfirmation();

const userId = tg.initDataUnsafe?.user?.id || new URLSearchParams(window.location.search).get('user_id');
let userData = {};

// Spam protection — ongoing requests tracker
const _pendingRequests = new Set();

// ---- TOAST NOTIFICATION ----
function showToast(msg, type = "info") {
    let toast = document.getElementById('toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.className = `show ${type}`;
    setTimeout(() => { toast.className = ''; }, 3000);
}

// ============================================================
// FETCH WITH RETRY — 3 retries, 10s timeout, 2s delay
// ============================================================
async function fetchWithRetry(url, options = {}, retries = 3, delayMs = 2000) {
    for (let attempt = 1; attempt <= retries; attempt++) {
        try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 10000);
            const res = await fetch(url, { ...options, signal: controller.signal });
            clearTimeout(timeout);
            if (!res.ok) {
                if (res.status >= 400 && res.status < 500) return res;
                throw new Error(`HTTP ${res.status}`);
            }
            return res;
        } catch (err) {
            if (attempt === retries) throw err;
            await new Promise(r => setTimeout(r, delayMs));
        }
    }
}

// ============================================================
// MAIN DATA FETCH
// ============================================================
async function fetchLiveData() {
    if (!userId) {
        const bal = document.getElementById('balance');
        if (bal) bal.innerText = "ID Error";
        return;
    }
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_user/${userId}`);
        const data = await res.json();

        if (data.status === "success") {
            userData = data;

            const coins    = data.coins || 0;
            const refCount = getRefCount(data.referrals);

            const balEl = document.getElementById('balance');
            if (balEl) balEl.innerText = `${coins} 🪙`;

            // Progress bars
            const coinsPct = Math.min((coins / 4000) * 100, 100);
            const refPct   = Math.min((refCount / 5) * 100, 100);

            const coinsBar  = document.getElementById('coins-progress-bar');
            const refBar    = document.getElementById('ref-progress-bar');
            const coinsText = document.getElementById('coins-progress-text');
            const refText   = document.getElementById('ref-progress-text');

            if (coinsBar) {
                coinsBar.style.width      = coinsPct + '%';
                coinsBar.style.background = coins >= 4000
                    ? 'linear-gradient(90deg,#2ecc71,#27ae60)'
                    : 'linear-gradient(90deg,#f1c40f,#f39c12)';
            }
            if (refBar)    refBar.style.width = refPct + '%';
            if (coinsText) coinsText.innerText = `${coins} / 4000 ${coins >= 4000 ? '✅' : ''}`;
            if (refText)   refText.innerText   = `${refCount} / 5 ${refCount >= 5 ? '✅' : ''}`;

            updateLeaderboardUI(data.leaderboard);

            const linkEl = document.getElementById('display-link');
            if (linkEl) linkEl.innerText = `https://t.me/${CONFIG.BOT_USERNAME}?start=${userId}`;

            updateReferralList(data.referrals);
            applyCompletedTasks(data.completed_tasks || []);
            checkDailyBonus(data.last_claim);
            updateAdCounter(data.ads_today || 0, data.ads_date || "");
            updateChannelButtons(data.channel_claims || {});
        }
    } catch (err) {
        showToast("⚠️ Server se connect nahi ho pa raha...", "error");
        setTimeout(fetchLiveData, 15000);
    }
}

// ---- Referral count safely nikalo ----
function getRefCount(referrals) {
    if (!referrals || referrals === "" || referrals === "none") return 0;
    return referrals.split(',').filter(id => id.trim() !== '').length;
}

// ============================================================
// DAILY BONUS — 24h countdown on button
// ============================================================
function checkDailyBonus(lastClaimTs) {
    const btn = document.getElementById('daily-btn');
    if (!btn) return;
    if (!lastClaimTs) { btn.disabled = false; btn.innerText = "Claim Now"; return; }
    try {
        const diffHours = (new Date() - new Date(lastClaimTs)) / 3600000;
        if (diffHours < 24) {
            const remaining = 24 - diffHours;
            const h = Math.floor(remaining);
            const m = Math.floor((remaining - h) * 60);
            btn.disabled = true;
            btn.innerText = `✅ ${h}h ${m}m baad`;
        } else {
            btn.disabled = false;
            btn.innerText = "Claim Now";
        }
    } catch (e) {
        btn.disabled = false;
        btn.innerText = "Claim Now";
    }
}

async function claimDaily() {
    if (!userId) return;
    if (_pendingRequests.has('claimDaily')) return;
    _pendingRequests.add('claimDaily');
    const btn = document.getElementById('daily-btn');
    if (btn) { btn.disabled = true; btn.innerText = "Claiming..."; }
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/claim_daily/${userId}`, { method: 'POST' });
        const data = await res.json();
        if (data.status === "success") {
            showToast("🎁 10 coins claim ho gaye!", "success");
            fetchLiveData();
        } else {
            showToast(data.message, "error");
            if (btn) { btn.disabled = false; btn.innerText = "Claim Now"; }
        }
    } catch (e) {
        showToast("⚠️ Error! Retry.", "error");
        if (btn) { btn.disabled = false; btn.innerText = "Claim Now"; }
    } finally {
        _pendingRequests.delete('claimDaily');
    }
}

// ============================================================
// AD COUNTER — 0/5 display, 10 coins per ad
// ============================================================
function updateAdCounter(adsToday, adsDate) {
    const container = document.getElementById('adsgram-container');
    if (!container) return;

    const today   = new Date().toISOString().split('T')[0];
    const done    = (adsDate === today) ? adsToday : 0;
    const MAX_ADS = 5;

    const counterEl = document.getElementById('ad-counter');
    if (counterEl) counterEl.innerText = `${done}/${MAX_ADS}`;

    if (done >= MAX_ADS) {
        container.innerHTML = `
            <div style="text-align:center; padding:10px 0; color:#64748b; font-size:13px;">
                ✅ Aaj ke ${MAX_ADS}/5 ads complete! Kal wapas aao.
            </div>`;
    }
}

// ============================================================
// WITHDRAW
// ============================================================
async function requestWithdraw() {
    if (!userId) return showToast("User ID nahi mila!", "error");
    if (_pendingRequests.has('withdraw')) return showToast("Request already processing...", "error");

    const upi          = document.getElementById('upi-id')?.value.trim();
    const amountInput  = document.getElementById('withdraw-amount');
    const rawAmount    = amountInput ? amountInput.value : '';
    const reqAmount    = parseInt(rawAmount);
    const totalCoins   = userData.coins || 0;

    if (!rawAmount)             return showToast("Withdrawal amount bharo!", "error");
    if (isNaN(reqAmount))       return showToast("Valid number enter karo!", "error");
    if (reqAmount <= 0)         return showToast("Amount zero ya negative nahi ho sakta!", "error");
    if (reqAmount < 4000)       return showToast(`Minimum 4000 coins chahiye. Aapne ${reqAmount} likha.`, "error");
    if (reqAmount > totalCoins) return showToast(`Aapke paas sirf ${totalCoins} coins hain!`, "error");
    if (!upi || !upi.includes('@')) return showToast("Valid UPI ID enter karo! (example: name@upi)", "error");

    _pendingRequests.add('withdraw');
    const btn = document.querySelector('[onclick="requestWithdraw()"]');
    if (btn) { btn.disabled = true; btn.innerText = "Processing..."; }

    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/withdraw`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ user_id: userId, upi_id: upi, amount: reqAmount })
        });
        const data = await res.json();
        if (data.status === "success") {
            showToast(`💸 ${reqAmount} coins ka withdrawal submit ho gaya!`, "success");
            const upiEl = document.getElementById('upi-id');
            if (upiEl) upiEl.value = '';
            if (amountInput) amountInput.value = '';
            fetchLiveData();
        } else {
            showToast(data.message || "Error! Retry.", "error");
        }
    } catch (e) {
        showToast("⚠️ Connection error! Retry.", "error");
    } finally {
        _pendingRequests.delete('withdraw');
        if (btn) { btn.disabled = false; btn.innerText = "Withdraw Now"; }
    }
}

// ============================================================
// TASKS — Daily reset + code-change reset
// ============================================================
function openTask(taskKey, type) {
    const link = type === 'yt' ? CONFIG.YT_LINKS[taskKey] : CONFIG.WEB_LINKS[taskKey];
    if (link && link !== '#') {
        window.open(link, '_blank');
    } else {
        showToast("Link update hoga jald!", "error");
    }
}

async function verifyTask(taskId, inputId) {
    const code = document.getElementById(inputId)?.value.trim();
    if (!code) return showToast("Code enter karo!", "error");

    const reqKey = `verify_${taskId}`;
    if (_pendingRequests.has(reqKey)) return;
    _pendingRequests.add(reqKey);

    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/verify_task`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ user_id: userId, task_id: taskId, code: code })
        });
        const data = await res.json();
        if (data.status === "success") {
            showToast(`✅ ${data.message}`, "success");
            fetchLiveData();
        } else {
            showToast(data.message, "error");
        }
    } catch (e) {
        showToast("⚠️ Error! Retry.", "error");
    } finally {
        _pendingRequests.delete(reqKey);
    }
}

// Mark completed tasks in UI — resets daily (backend controls this)
function applyCompletedTasks(completedList) {
    // First un-mark all tasks
    document.querySelectorAll('.task-item').forEach(el => el.classList.remove('done'));
    // Then mark today's done tasks
    completedList.forEach(taskId => {
        const item = document.querySelector(`[data-task="${taskId}"]`);
        if (item) item.classList.add('done');
    });
}

// ============================================================
// TELEGRAM CHANNEL JOIN — One-time coin reward
// ============================================================
function updateChannelButtons(channelClaims) {
    const channels = ['official', 'channel2', 'channel3'];
    channels.forEach(ch => {
        const btn = document.getElementById(`ch-btn-${ch}`);
        if (!btn) return;
        if (channelClaims[ch]) {
            btn.disabled    = true;
            btn.innerText   = "✅ Joined";
            btn.style.background = "#2ecc71";
        }
    });
}

async function claimChannel(channelId, channelUrl) {
    if (!userId) return showToast("User ID nahi mila!", "error");
    const reqKey = `channel_${channelId}`;
    if (_pendingRequests.has(reqKey)) return;

    // Pehle channel open karo
    window.open(channelUrl, '_blank');

    // 2 second baad claim karo (user ko join karne ka time do)
    setTimeout(async () => {
        _pendingRequests.add(reqKey);
        const btn = document.getElementById(`ch-btn-${channelId}`);
        if (btn) { btn.disabled = true; btn.innerText = "Claiming..."; }

        try {
            const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/claim_channel`, {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ user_id: userId, channel_id: channelId })
            });
            const data = await res.json();
            if (data.status === "success") {
                showToast(`🎉 ${data.message}`, "success");
                if (btn) { btn.disabled = true; btn.innerText = "✅ Joined"; btn.style.background = "#2ecc71"; }
                fetchLiveData();
            } else {
                showToast(data.message, "error");
                if (btn) { btn.disabled = false; btn.innerText = "Join & Claim"; }
            }
        } catch (e) {
            showToast("⚠️ Error! Retry.", "error");
            if (btn) { btn.disabled = false; btn.innerText = "Join & Claim"; }
        } finally {
            _pendingRequests.delete(reqKey);
        }
    }, 2000);
}

// ============================================================
// LEADERBOARD
// ============================================================
function updateLeaderboardUI(leaderboardData) {
    const list = document.getElementById('leaderboard-list');
    if (!list) return;
    if (!leaderboardData || leaderboardData === "none") {
        list.innerHTML = "<p class='spinner'>No users yet.</p>";
        return;
    }
    const medals  = ['🥇', '🥈', '🥉'];
    const players = leaderboardData.split('|');
    let html = "";
    players.forEach((p, i) => {
        const [id, coins] = p.split(':');
        const isMe = String(id) === String(userId);
        html += `
            <div class="lb-item" style="${isMe ? 'background:rgba(99,102,241,0.1);border-radius:8px;padding:10px;' : ''}">
                <span class="lb-rank">${medals[i] || `#${i + 1}`}</span>
                <span class="lb-user">${isMe ? '👤 You' : `User ${id}`}</span>
                <span class="lb-coins">${parseInt(coins) || 0} 🪙</span>
            </div>`;
    });
    list.innerHTML = html;
}

// ============================================================
// REFERRAL LIST
// ============================================================
function updateReferralList(referrals) {
    const list = document.getElementById('refer-list');
    if (!list) return;
    const refCount = getRefCount(referrals);
    if (refCount === 0) {
        list.innerHTML = "<p style='color:#94a3b8;text-align:center;font-size:13px;'>No referrals yet. Invite friends! 🚀</p>";
        return;
    }
    const refs = referrals.split(',').filter(id => id.trim() !== '');
    let html = "";
    refs.forEach((id, i) => {
        html += `
            <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #1e293b;">
                <span style="font-size:16px;">👤</span>
                <div>
                    <p style="margin:0;font-size:13px;font-weight:600;color:#e2e8f0;">Friend ${i + 1}</p>
                    <p style="margin:0;font-size:11px;color:#94a3b8;">ID: ${id.trim()}</p>
                </div>
                <span style="margin-left:auto;font-size:12px;color:#2ecc71;font-weight:700;">+50 🪙</span>
            </div>`;
    });
    list.innerHTML = html;
}

// ============================================================
// WITHDRAWAL HISTORY
// ============================================================
async function loadHistory() {
    const list = document.getElementById('history-list');
    if (!list || !userId) return;
    list.innerHTML = "<p class='spinner'>Loading...</p>";
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_history/${userId}`);
        const data = await res.json();
        if (data.history && data.history.length > 0) {
            let html = "";
            data.history.forEach(h => {
                const color = h.status.includes('Approved') ? '#22c55e' : h.status.includes('Rejected') ? '#e74c3c' : '#f1c40f';
                html += `
                    <div class="history-item">
                        <div>💸 <b>${h.amount} coins</b> — UPI: ${h.upi_id}</div>
                        <div class="history-status" style="color:${color}">${h.status} • ${h.date}</div>
                    </div>`;
            });
            list.innerHTML = html;
        } else {
            list.innerHTML = "<p style='color:#94a3b8;text-align:center;'>No history found.</p>";
        }
    } catch (e) {
        list.innerHTML = "<p style='color:#94a3b8;text-align:center;'>Error loading history.</p>";
    }
}

// ============================================================
// SUPPORT
// ============================================================
async function sendSupport() {
    if (_pendingRequests.has('support')) return;
    const msgEl = document.getElementById('support-msg');
    const msg   = msgEl ? msgEl.value.trim() : '';
    if (!msg)              return showToast("Message likho!", "error");
    if (!userId)           return showToast("User ID nahi mila!", "error");
    if (msg.length > 1000) return showToast("Message bahut lamba! (max 1000 chars)", "error");

    _pendingRequests.add('support');
    const btn = document.querySelector('[onclick="sendSupport()"]');
    if (btn) { btn.disabled = true; btn.innerText = "Sending..."; }

    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/send_support`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ user_id: userId, message: msg })
        });
        const data = await res.json();
        if (data.status === "success") {
            showToast("✅ Support message sent to Admin!", "success");
            if (msgEl) msgEl.value = '';
        } else {
            showToast(data.message || "Error sending message.", "error");
        }
    } catch (e) {
        showToast("⚠️ Message nahi gaya! Connection check karo.", "error");
    } finally {
        _pendingRequests.delete('support');
        if (btn) { btn.disabled = false; btn.innerText = "Send to Admin"; }
    }
}

// ============================================================
// ADSGRAM — 10 coins, 0/5 counter
// ============================================================
let AdController = null;

async function initAdsgram() {
    try {
        if (window.Adsgram) {
            AdController = window.Adsgram.init({ blockId: CONFIG.ADSGRAM_BLOCK_ID });
        }
    } catch (e) {}
}

async function showAd() {
    if (_pendingRequests.has('showAd')) return;
    if (!AdController) { showToast("Ad abhi available nahi hai.", "error"); return; }
    _pendingRequests.add('showAd');
    try {
        await AdController.show();
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/watch_ad/${userId}`, { method: 'POST' });
        const data = await res.json();
        if (data.status === "success") {
            showToast(`✅ ${data.message}`, "success");
            // Update counter immediately
            const counterEl = document.getElementById('ad-counter');
            if (counterEl && data.ads_done !== undefined) counterEl.innerText = `${data.ads_done}/5`;
            fetchLiveData();
        } else {
            showToast(data.message, "error");
        }
    } catch (e) {
        showToast("Ad skip kiya — koi coins nahi.", "error");
    } finally {
        _pendingRequests.delete('showAd');
    }
}

// ============================================================
// DEVICE CHECK — Fingerprint (soft) + IP (hard, backend)
// ============================================================
async function generateFingerprint() {
    try {
        const data = [navigator.userAgent, navigator.language, screen.width + "x" + screen.height,
            screen.colorDepth, new Date().getTimezoneOffset(), navigator.hardwareConcurrency || "", navigator.platform || ""].join("|");
        const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(data));
        return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join("");
    } catch (e) { return ""; }
}

async function checkDevice() {
    if (!userId) return;
    try {
        const fingerprint = await generateFingerprint();
        const res  = await fetch(`${CONFIG.API_BASE_URL}/check_device`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId, fingerprint })
        });
        const data = await res.json();
        if (data.status === "blocked") {
            document.body.innerHTML = `
                <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;background:#0f172a;color:#e2e8f0;text-align:center;padding:20px;">
                    <div style="font-size:60px;">🚫</div>
                    <h2 style="color:#e74c3c;margin:15px 0;">Account Blocked</h2>
                    <p style="color:#94a3b8;font-size:14px;">Multiple accounts allowed nahi hain.</p>
                </div>`;
        }
    } catch (e) {}
}

// ============================================================
// UTILITY
// ============================================================
function copyEmail() {
    navigator.clipboard.writeText('cdotern.help@gmail.com').catch(() => {});
    const status = document.getElementById('copy-status');
    if (status) { status.style.display = 'block'; setTimeout(() => { status.style.display = 'none'; }, 2000); }
}

function inviteFriend() {
    const link = `https://t.me/${CONFIG.BOT_USERNAME}?start=${userId}`;
    if (tg && tg.openTelegramLink) {
        tg.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent('Join Daksh Grand Earn aur coins kamao! 🚀')}`);
    } else if (navigator.share) {
        navigator.share({ text: `Join Daksh Grand Earn! 🚀\n${link}` });
    } else {
        navigator.clipboard.writeText(link).catch(() => {});
        showToast("✅ Link copied!", "success");
    }
}

function switchTab(tabId, el) {
    document.querySelectorAll('.tab-content').forEach(t => { t.style.display = 'none'; t.classList.remove('active-tab'); });
    const tab = document.getElementById(tabId);
    if (tab) { tab.style.display = 'block'; tab.classList.add('active-tab'); }
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    if (el && el.classList.contains('nav-item')) el.classList.add('active');
    const titles = { rewards: 'Rewards', tasks: 'Tasks', leaderboard: 'Leaderboard', refer: 'Refer & Earn', history: 'History', help: 'Support' };
    const titleEl = document.getElementById('tab-title');
    if (titleEl) titleEl.innerText = titles[tabId] || tabId;
    if (tabId === 'history') loadHistory();
}

// ============================================================
// INIT
// ============================================================
checkDevice();
fetchLiveData();
initAdsgram();
